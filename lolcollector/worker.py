"""Boucle principale : 3 workers asyncio (un par routing régional) + veille de patch."""

import asyncio
import itertools
import logging
import logging.handlers
import os
import signal
import time

import aiohttp

from .config import BUCKETS, QUEUE_ID, REGIONS, Config
from .db import Database
from .ratelimit import RateLimiter
from .riot import FatalApiError, RiotClient
from .sampler import BucketSampler
from .db import patch_of
from .timeline import PatchQuota, is_sampled, mark_timeline, store_timeline

STATS_LOG_EVERY = 50  # log de progression toutes les N insertions par région


def setup_logging(log_dir: str, level: str = "INFO") -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("collector")
    logger.setLevel(getattr(logging, level, logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "collector.log"),
        maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(console)
    return logger


async def patch_watcher(client: RiotClient, db: Database, interval: int,
                        stop_event: asyncio.Event, log: logging.Logger):
    """Récupère la version ddragon au démarrage puis toutes les `interval` secondes."""
    while not stop_event.is_set():
        try:
            versions = await client.ddragon_versions()
            current = versions[0] if versions else None
            if current:
                previous = db.get_meta("ddragon_current")
                if previous and previous != current:
                    log.info("=" * 60)
                    log.info("PATCH_CHANGE : %s -> %s", previous, current)
                    log.info("=" * 60)
                elif not previous:
                    log.info("Version ddragon courante : %s", current)
                db.set_meta("ddragon_current", current)
        except Exception as exc:
            log.warning("patch_watcher : échec récupération versions.json (%s)", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def region_worker(cfg: Config, db: Database, client: RiotClient,
                        region: str, platform: str,
                        stop_event: asyncio.Event, log: logging.Logger):
    samplers = {
        bucket: BucketSampler(db, client, region, platform, bucket, tiers,
                              cfg.max_pages_per_division, log)
        for bucket, tiers in BUCKETS.items()
    }
    bucket_cycle = itertools.cycle(BUCKETS.keys())  # round-robin entre buckets
    inserted = 0
    skipped_dup = 0
    timelines = 0
    quota = PatchQuota(db, cfg.timeline_target_per_patch)
    quota_logged: dict[str, bool] = {}

    log.info("[%s] worker démarré (plateforme %s, timelines %.0f%% "
             "avec plafond de %d par patch)",
             region, platform, cfg.timeline_sample_rate * 100,
             cfg.timeline_target_per_patch)
    while not stop_event.is_set():
        bucket = next(bucket_cycle)
        try:
            puuid = await samplers[bucket].next_puuid()
            if not puuid:
                await asyncio.sleep(30)
                continue
            # Fenêtre glissante calculée à chaque appel : Riot filtre côté
            # serveur (startTime), les vieux matchs ne coûtent rien.
            start_time = int(time.time()) - cfg.match_max_age_days * 86400
            match_ids = await client.match_ids(
                region, puuid, cfg.matches_per_player, QUEUE_ID, start_time
            ) or []
            if not match_ids:
                # Joueur inactif sur la fenêtre : aucun détail dépensé
                log.debug("[%s] %s… : aucun match depuis %d jours, joueur suivant",
                          region, puuid[:12], cfg.match_max_age_days)
                continue
            # Dédup AVANT de dépenser la requête de détail
            fresh = [mid for mid in match_ids if not db.has_match(mid)]
            skipped_dup += len(match_ids) - len(fresh)
            for match_id in fresh:
                if stop_event.is_set():
                    break
                data = await client.match(region, match_id)
                if data and db.store_match(data, region, platform, bucket):
                    inserted += 1
                    # Timeline : fraction échantillonnée, et tant que le
                    # plafond du patch n'est pas atteint
                    match_patch = patch_of((data.get("info") or {}).get("gameVersion", ""))
                    if quota.reached(match_patch):
                        if not quota_logged.get(match_patch):
                            log.info("[%s] plafond de timelines atteint pour le "
                                     "patch %s (%d) — collecte suspendue jusqu'au "
                                     "patch suivant", region, match_patch,
                                     cfg.timeline_target_per_patch)
                            quota_logged[match_patch] = True
                        mark_timeline(db, match_id, "skipped")
                    elif is_sampled(match_id, cfg.timeline_sample_rate):
                        try:
                            tl = await client.match_timeline(region, match_id)
                            if tl:
                                n_ev, n_fr = store_timeline(db, match_id, tl)
                                quota.record_stored(match_patch)
                                timelines += 1
                                log.debug("[%s] timeline %s : %d events, %d frames",
                                          region, match_id, n_ev, n_fr)
                            else:
                                mark_timeline(db, match_id, "missing")
                        except FatalApiError:
                            raise
                        except Exception as exc:
                            # une timeline ratée ne doit pas coûter le match
                            log.warning("[%s] timeline %s échouée : %s",
                                        region, match_id, exc)
                    else:
                        mark_timeline(db, match_id, "skipped")
                    if inserted % STATS_LOG_EVERY == 0:
                        log.info("[%s] %d matchs insérés (%d doublons évités, "
                                 "%d timelines)", region, inserted, skipped_dup,
                                 timelines)
        except FatalApiError:
            raise
        except Exception as exc:
            log.error("[%s] erreur worker (bucket %s) : %s", region, bucket, exc)
            await asyncio.sleep(5)
    log.info("[%s] worker arrêté (%d matchs insérés, %d timelines cette session)",
             region, inserted, timelines)


async def run_collector():
    cfg = Config()
    cfg.require_api_key()
    log = setup_logging(cfg.log_dir, cfg.log_level)
    db = Database(cfg.db_path)

    with open(cfg.pid_file, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    log.info("lol-studies-collector démarré (pid %d)", os.getpid())
    try:
        async with aiohttp.ClientSession() as session:
            # Un rate limiter indépendant PAR région (les limites Riot sont par région).
            # Sur europe le budget est réduit : lol-live-coach partage la même clé.
            clients = {}
            for region in REGIONS:
                per_s, per_2min = cfg.rate_limits[region]
                log.info("[%s] budget requêtes : %d req/s, %d req/2min",
                         region, per_s, per_2min)
                limiter = RateLimiter([(per_s, 1.0), (per_2min, 120.0)], name=region)
                clients[region] = RiotClient(session, cfg.api_key, limiter, log)
            tasks = [
                asyncio.create_task(
                    patch_watcher(next(iter(clients.values())), db,
                                  cfg.patch_check_interval, stop_event, log),
                    name="patch_watcher",
                )
            ]
            tasks += [
                asyncio.create_task(
                    region_worker(cfg, db, clients[region], region, platform,
                                  stop_event, log),
                    name=f"worker_{region}",
                )
                for region, platform in REGIONS.items()
            ]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                exc = task.exception()
                if exc:
                    log.critical("Tâche %s tuée par : %s — arrêt du collecteur",
                                 task.get_name(), exc)
            stop_event.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
    finally:
        db.close()
        try:
            os.remove(cfg.pid_file)
        except OSError:
            pass
        log.info("Collecteur arrêté proprement.")
