"""Rate limiter asynchrone à fenêtres glissantes, un par routing régional.

Limites Riot (clé personnelle) : 20 req/s et 100 req/2min, PAR RÉGION.
Les budgets effectifs (avec marge, et réserve coach sur europe) sont dans
config.DEFAULT_RATE_LIMITS.

Sémantique GLISSANTE : quand une fenêtre est pleine, on attend uniquement
jusqu'à l'expiration de la requête LA PLUS ANCIENNE de la fenêtre — jamais
la vidange complète, et aucun sleep fixe par requête. En régime permanent,
une fenêtre (N, W) débite donc N requêtes par tranche de W secondes.
"""

import asyncio
import logging
import time
from collections import deque

DEFAULT_LIMITS = [(18, 1.0), (90, 120.0)]  # (requêtes, fenêtre en s)

log = logging.getLogger("collector.ratelimit")


class RateLimiter:
    def __init__(self, limits=None, name: str = ""):
        self._limits = [(count, window, deque()) for count, window in (limits or DEFAULT_LIMITS)]
        self._lock = asyncio.Lock()
        self._blocked_until = 0.0  # posé par penalize() sur un 429 (Retry-After)
        self.name = name

    def _occupancy(self) -> str:
        return ", ".join(
            f"{len(sent)}/{count} sur {window:g}s" for count, window, sent in self._limits
        )

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                blocked = self._blocked_until - now
                if blocked > 0:
                    log.debug("[%s] acquire: bloqué %.1fs (Retry-After 429)",
                              self.name, blocked)
                    wait = blocked
                else:
                    wait = 0.0
                    for count, window, sent in self._limits:
                        # Purge des requêtes sorties de la fenêtre glissante
                        while sent and sent[0] <= now - window:
                            sent.popleft()
                        if len(sent) >= count:
                            # Fenêtre pleine : attendre l'expiration de la plus
                            # ANCIENNE requête seulement (jamais de vidange totale)
                            wait = max(wait, sent[0] + window - now)
                    if log.isEnabledFor(logging.DEBUG):
                        log.debug("[%s] acquire: attente %.3fs (occupation: %s)",
                                  self.name, wait, self._occupancy())
                    if wait <= 0:
                        for _, _, sent in self._limits:
                            sent.append(now)
                        return
            await asyncio.sleep(max(wait, 0.02))

    def penalize(self, seconds: float) -> None:
        """Bloque toutes les requêtes de la région pendant `seconds` (429 Retry-After)."""
        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)
        log.warning("[%s] penalize: région bloquée %.1fs (429 Retry-After)",
                    self.name, seconds)
