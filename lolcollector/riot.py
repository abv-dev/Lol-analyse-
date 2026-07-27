"""Client HTTP Riot : rate limiting, 429 Retry-After, retry 5xx avec backoff."""

import asyncio
import logging

import aiohttp

MAX_RETRIES = 6          # retries sur 5xx / erreurs réseau
MAX_429_STREAK = 20      # garde-fou contre une boucle infinie de 429


class RiotApiError(Exception):
    """Erreur non récupérable sur une requête (après retries)."""


class FatalApiError(Exception):
    """Clé API invalide/expirée : inutile de continuer, il faut régénérer la clé."""


class RiotClient:
    def __init__(self, session: aiohttp.ClientSession, api_key: str, limiter, logger=None):
        self._session = session
        self._headers = {"X-Riot-Token": api_key}
        self._limiter = limiter
        self._log = logger or logging.getLogger("riot")

    async def _get(self, url: str, params: dict | None = None):
        attempt = 0
        streak_429 = 0
        while True:
            await self._limiter.acquire()
            try:
                async with self._session.get(
                    url, params=params, headers=self._headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 404:
                        return None
                    if resp.status == 429:
                        streak_429 += 1
                        if streak_429 > MAX_429_STREAK:
                            raise RiotApiError(f"429 en boucle sur {url}")
                        retry_after = float(resp.headers.get("Retry-After", "10"))
                        self._log.warning("429 sur %s, Retry-After=%ss", url, retry_after)
                        self._limiter.penalize(retry_after + 0.5)
                        continue
                    if resp.status in (401, 403):
                        raise FatalApiError(
                            f"HTTP {resp.status} : clé API invalide ou expirée "
                            "(les clés personnelles expirent toutes les 24h)"
                        )
                    if resp.status >= 500:
                        attempt += 1
                        if attempt > MAX_RETRIES:
                            raise RiotApiError(f"HTTP {resp.status} persistant sur {url}")
                        backoff = min(2 ** attempt, 60)
                        self._log.warning("HTTP %s sur %s, retry dans %ss", resp.status, url, backoff)
                        await asyncio.sleep(backoff)
                        continue
                    raise RiotApiError(f"HTTP {resp.status} inattendu sur {url}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                attempt += 1
                if attempt > MAX_RETRIES:
                    raise RiotApiError(f"Erreur réseau persistante sur {url}: {exc}") from exc
                backoff = min(2 ** attempt, 60)
                self._log.warning("Erreur réseau %s sur %s, retry dans %ss", exc, url, backoff)
                await asyncio.sleep(backoff)

    async def league_entries(self, platform: str, tier: str, division: str, page: int):
        """League-Exp-V4 : entrées d'un tier/division (inclut le puuid depuis 2024)."""
        url = (f"https://{platform}.api.riotgames.com"
               f"/lol/league-exp/v4/entries/RANKED_SOLO_5x5/{tier}/{division}")
        return await self._get(url, params={"page": page})

    async def match_ids(self, region: str, puuid: str, count: int, queue: int,
                        start_time: int | None = None):
        """Liste d'ids Match-V5. `start_time` (epoch seconds) est filtré CÔTÉ
        SERVEUR par Riot : les matchs plus vieux ne coûtent aucune requête."""
        url = (f"https://{region}.api.riotgames.com"
               f"/lol/match/v5/matches/by-puuid/{puuid}/ids")
        params = {"queue": queue, "count": count, "type": "ranked"}
        if start_time is not None:
            params["startTime"] = start_time
        return await self._get(url, params=params)

    async def match(self, region: str, match_id: str):
        url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}"
        return await self._get(url)

    async def ddragon_versions(self):
        """versions.json de Data Dragon (hors rate limit Riot API)."""
        from .config import DDRAGON_VERSIONS_URL
        async with self._session.get(
            DDRAGON_VERSIONS_URL, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            resp.raise_for_status()
            return await resp.json()
