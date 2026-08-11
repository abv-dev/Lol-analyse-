"""Rétro-collecte des timelines de matchs déjà en base.

    python3 collector.py backfill-timelines --limit 5000

Traite en priorité le patch courant, puis les patchs précédents (du plus
récent au plus ancien). Seuls les matchs retenus par l'échantillonnage
déterministe (`TIMELINE_SAMPLE_RATE`) sont récupérés : la rétro-collecte
porte donc exactement sur les mêmes matchs que la collecte en direct.

Rate limiting : chaque région a son propre limiter, aux mêmes budgets que les
workers. Lancé pendant que le collecteur tourne, le backfill **partage donc
le budget régional** (les limites Riot sont par clé et par région) : c'est
voulu, mais il faut l'affamer volontairement pour ne pas ralentir la collecte
de matchs — d'où `--share` (fraction du budget qui lui est allouée, 0.3 par
défaut, soit ~30 % des requêtes de la région).
"""

import asyncio
import logging
import time

import aiohttp

from .config import REGIONS, Config
from .db import Database, patch_of
from .ratelimit import RateLimiter
from .riot import FatalApiError, RiotClient
from .timeline import is_sampled, mark_timeline, store_timeline, stored_count_for_patch
from .worker import setup_logging

PROGRESS_EVERY = 50


def candidates(db: Database, limit: int, rate: float, target_per_patch: int = 0):
    """Matchs sans timeline connue, patch courant d'abord, puis récents.

    Le filtre d'échantillonnage est appliqué en Python (fonction de hash) :
    on sur-sélectionne en SQL puis on filtre, en s'appuyant sur l'index
    (patch, …) et sur l'absence de ligne dans timeline_state.
    """
    row = db.conn.execute(
        "SELECT value FROM meta WHERE key = 'ddragon_current'").fetchone()
    current_patch = patch_of(row[0]) if row else None

    selected: list[tuple[str, str]] = []
    seen_patches: list[str] = []
    if current_patch:
        seen_patches.append(current_patch)
    seen_patches += [
        p for (p,) in db.conn.execute(
            "SELECT DISTINCT patch FROM matches ORDER BY patch DESC")
        if p and p != current_patch
    ]

    for patch in seen_patches:
        if len(selected) >= limit:
            break
        # Le plafond par patch s'applique aussi au backfill : sinon il
        # contournerait la limite que la collecte en direct respecte.
        room = limit - len(selected)
        if target_per_patch > 0:
            already = stored_count_for_patch(db, patch)
            room = min(room, max(0, target_per_patch - already))
            if room == 0:
                continue
        taken = 0
        # on lit par lots : beaucoup de matchs seront hors échantillon
        cursor = db.conn.execute(
            "SELECT m.match_id, m.region FROM matches m"
            " LEFT JOIN timeline_state t ON t.match_id = m.match_id"
            " WHERE m.patch = ? AND t.match_id IS NULL",
            (patch,),
        )
        for match_id, region in cursor:
            if is_sampled(match_id, rate):
                selected.append((match_id, region))
                taken += 1
                if taken >= room or len(selected) >= limit:
                    break
    return selected


async def _region_worker(region, jobs, cfg, db, client, log, counters, stop):
    while jobs and not stop.is_set():
        match_id = jobs.pop()
        try:
            data = await client.match_timeline(region, match_id)
            if data:
                store_timeline(db, match_id, data)
                counters["ok"] += 1
            else:
                mark_timeline(db, match_id, "missing")
                counters["missing"] += 1
        except FatalApiError:
            raise
        except Exception as exc:
            log.warning("[%s] timeline %s échouée : %s", region, match_id, exc)
            counters["failed"] += 1
        done = counters["ok"] + counters["missing"] + counters["failed"]
        if done % PROGRESS_EVERY == 0:
            log.info("backfill : %d/%d traités (%d ok, %d absentes, %d échecs)",
                     done, counters["total"], counters["ok"],
                     counters["missing"], counters["failed"])


async def run_backfill(limit: int, share: float) -> int:
    cfg = Config()
    cfg.require_api_key()
    log = setup_logging(cfg.log_dir, cfg.log_level)
    db = Database(cfg.db_path)
    try:
        jobs = candidates(db, limit, cfg.timeline_sample_rate,
                          cfg.timeline_target_per_patch)
        if not jobs:
            log.info("backfill : aucune timeline à récupérer")
            print("Aucune timeline à récupérer : tout l'échantillon est traité, "
                  "ou le plafond par patch "
                  f"(TIMELINE_TARGET_PER_PATCH={cfg.timeline_target_per_patch}) "
                  "est atteint.")
            return 0

        by_region: dict[str, list[str]] = {}
        for match_id, region in jobs:
            by_region.setdefault(region, []).append(match_id)
        counters = {"total": len(jobs), "ok": 0, "missing": 0, "failed": 0}
        log.info("backfill : %d timelines à récupérer (%s), part du budget %.0f%%",
                 len(jobs),
                 ", ".join(f"{r}:{len(v)}" for r, v in sorted(by_region.items())),
                 share * 100)
        print(f"{len(jobs)} timelines à récupérer "
              f"({', '.join(f'{r}:{len(v)}' for r, v in sorted(by_region.items()))})…")

        started = time.time()
        stop = asyncio.Event()
        async with aiohttp.ClientSession() as session:
            tasks = []
            for region, match_ids in by_region.items():
                per_s, per_2min = cfg.rate_limits.get(region, (14, 68))
                # budget volontairement réduit : le collecteur en direct doit
                # garder la main sur la région
                limiter = RateLimiter(
                    [(max(1, int(per_s * share)), 1.0),
                     (max(1, int(per_2min * share)), 120.0)],
                    name=f"backfill:{region}")
                client = RiotClient(session, cfg.api_key, limiter, log)
                tasks.append(asyncio.create_task(
                    _region_worker(region, match_ids, cfg, db, client, log,
                                   counters, stop)))
            try:
                await asyncio.gather(*tasks)
            except FatalApiError as exc:
                stop.set()
                log.critical("backfill interrompu : %s", exc)
                print(f"Interrompu : {exc}")
                return 1

        elapsed = time.time() - started
        msg = (f"backfill terminé : {counters['ok']} timelines stockées, "
               f"{counters['missing']} absentes, {counters['failed']} échecs "
               f"en {elapsed / 60:.1f} min")
        log.info(msg)
        print(msg)
        return 0
    finally:
        db.close()
