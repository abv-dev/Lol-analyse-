"""Boucle principale : 3 workers asyncio (un par routing régional) + veille de patch."""

import asyncio
import itertools
import logging
import logging.handlers
import os
import signal

import aiohttp

from .config import BUCKETS, QUEUE_ID, REGIONS, Config
from .db import Database
from .ratelimit import RateLimiter
from .riot import FatalApiError, RiotClient
from .sampler import BucketSampler

STATS_LOG_EVERY = 50  # log de progression toutes les N insertions par région


def setup_logging(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("collector")
    logger.setLevel(logging.INFO)
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

    log.info("[%s] worker démarré (plateforme %s)", region, platform)
    while not stop_event.is_set():
        bucket = next(bucket_cycle)
        try:
            puuid = await samplers[bucket].next_puuid()
            if not puuid:
                await asyncio.sleep(30)
                continue
            match_ids = await client.match_ids(
                region, puuid, cfg.matches_per_player, QUEUE_ID
            ) or []
            # Dédup AVANT de dépenser la requête de détail
            fresh = [mid for mid in match_ids if not db.has_match(mid)]
            skipped_dup += len(match_ids) - len(fresh)
            for match_id in fresh:
                if stop_event.is_set():
                    break
                data = await client.match(region, match_id)
                if data and db.store_match(data, region, platform, bucket):
                    inserted += 1
                    if inserted % STATS_LOG_EVERY == 0:
                        log.info("[%s] %d matchs insérés (%d doublons évités)",
                                 region, inserted, skipped_dup)
        except FatalApiError:
            raise
        except Exception as exc:
            log.error("[%s] erreur worker (bucket %s) : %s", region, bucket, exc)
            await asyncio.sleep(5)
    log.info("[%s] worker arrêté (%d matchs insérés cette session)", region, inserted)


async def run_collector():
    cfg = Config()
    cfg.require_api_key()
    log = setup_logging(cfg.log_dir)
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
            # Un rate limiter indépendant PAR région (les limites Riot sont par région)
            clients = {
                region: RiotClient(session, cfg.api_key, RateLimiter(), log)
                for region in REGIONS
            }
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
