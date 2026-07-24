"""Rate limiter asynchrone à fenêtres glissantes, un par routing régional.

Limites Riot (clé personnelle) : 20 req/s et 100 req/2min, PAR RÉGION.
On prend une marge de 10 % : 18 req/s et 90 req/2min.
"""

import asyncio
import time
from collections import deque

DEFAULT_LIMITS = [(18, 1.0), (90, 120.0)]  # (requêtes, fenêtre en s), marge 10 %


class RateLimiter:
    def __init__(self, limits=None):
        self._limits = [(count, window, deque()) for count, window in (limits or DEFAULT_LIMITS)]
        self._lock = asyncio.Lock()
        self._blocked_until = 0.0  # posé par penalize() sur un 429 (Retry-After)

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                wait = self._blocked_until - now
                if wait <= 0:
                    for count, window, sent in self._limits:
                        while sent and sent[0] <= now - window:
                            sent.popleft()
                        if len(sent) >= count:
                            wait = max(wait, sent[0] + window - now)
                    if wait <= 0:
                        for _, _, sent in self._limits:
                            sent.append(now)
                        return
            await asyncio.sleep(max(wait, 0.02))

    def penalize(self, seconds: float) -> None:
        """Bloque toutes les requêtes de la région pendant `seconds` (429 Retry-After)."""
        self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)
