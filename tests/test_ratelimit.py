#!/usr/bin/env python3
"""Tests offline du rate limiter.

  python3 tests/test_ratelimit.py            # tests rapides (horloge virtuelle + chrono court)
  python3 tests/test_ratelimit.py --real     # chrono réel : 300 acquires aux limites de prod
                                             # (90 req/2min) -> doit passer en ~6-7 min, pas ~2h30

Le test --real est celui qui invalide un limiter "à vidange complète" ou un
sleep fixe par requête : un tel limiter mettrait >2h pour 300 acquires.
"""

import asyncio
import sys
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lolcollector.ratelimit import RateLimiter  # noqa: E402


# ---------------------------------------------------------------------------
# 1) Horloge virtuelle : rejoue exactement le scénario "300 acquires, 90/2min"
#    sans attendre le temps réel, et vérifie la sémantique glissante.
# ---------------------------------------------------------------------------

def test_virtual_clock_300_acquires():
    import lolcollector.ratelimit as rl

    virtual_now = [0.0]
    real_monotonic = time.monotonic
    real_sleep = asyncio.sleep

    def fake_monotonic():
        return virtual_now[0]

    async def fake_sleep(seconds):
        virtual_now[0] += seconds
        await real_sleep(0)  # rend la main à l'event loop

    rl.time.monotonic = fake_monotonic
    rl.asyncio.sleep = fake_sleep
    try:
        limiter = RateLimiter([(18, 1.0), (90, 120.0)], name="virtual")
        waits = []

        async def run():
            for _ in range(300):
                before = virtual_now[0]
                await limiter.acquire()
                waits.append(virtual_now[0] - before)

        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(run())
        total = virtual_now[0]
    finally:
        rl.time.monotonic = real_monotonic
        rl.asyncio.sleep = real_sleep

    # Fenêtre glissante : 300 acquires à 90/120s ~= 360s (4 rafales de 90/90/90/30).
    # Un limiter à vidange complète ou avec sleep fixe par requête exploserait ce budget.
    assert 300 <= total <= 400, f"temps virtuel {total:.1f}s hors de [300, 400]s"
    # Aucune attente individuelle ne doit dépasser la fenêtre : on n'attend que
    # l'expiration de la plus ancienne requête, jamais la vidange de la fenêtre.
    assert max(waits) <= 121, f"attente max {max(waits):.1f}s > fenêtre (vidange complète ?)"
    # Débit soutenu ~45 req/min
    rate_per_min = 300 / (total / 60)
    assert 44 <= rate_per_min <= 60, f"débit virtuel {rate_per_min:.1f} req/min, attendu ~45-54"
    print(f"OK  virtuel : 300 acquires en {total:.1f}s simulées "
          f"(~{rate_per_min:.0f} req/min), attente max {max(waits):.1f}s")


# ---------------------------------------------------------------------------
# 2) Chrono réel court : mêmes ratios, fenêtres réduites (9 req / 3s).
#    36 acquires -> ~9s en glissant, ~36s+ en vidange complète.
# ---------------------------------------------------------------------------

async def _chrono(limiter, n):
    t0 = time.monotonic()
    for _ in range(n):
        await limiter.acquire()
    return time.monotonic() - t0


def test_real_clock_scaled():
    limiter = RateLimiter([(9, 3.0)], name="scaled")
    elapsed = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _chrono(limiter, 36)
    )
    # Glissant : rafales de 9 à t=0,3,6,9 -> ~9s. Vidange complète : >= 12s.
    assert 8.5 <= elapsed <= 11.0, f"chrono réel réduit {elapsed:.1f}s hors de [8.5, 11]s"
    print(f"OK  chrono réduit : 36 acquires (9 req/3s) en {elapsed:.1f}s (attendu ~9s)")


# ---------------------------------------------------------------------------
# 3) Chrono réel aux limites de prod (opt-in, ~6 min).
# ---------------------------------------------------------------------------

def test_real_clock_production():
    limiter = RateLimiter([(18, 1.0), (90, 120.0)], name="prod")
    print("chrono réel : 300 acquires à 90 req/2min, ~6 min…", flush=True)
    elapsed = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        _chrono(limiter, 300)
    )
    minutes = elapsed / 60
    rate_per_min = 300 / minutes
    assert 330 <= elapsed <= 450, (
        f"300 acquires en {minutes:.1f} min ({rate_per_min:.1f} req/min) : "
        f"attendu ~6-7 min à ~45 req/min"
    )
    print(f"OK  chrono prod : 300 acquires en {minutes:.1f} min (~{rate_per_min:.0f} req/min)")


if __name__ == "__main__":
    test_virtual_clock_300_acquires()
    test_real_clock_scaled()
    if "--real" in sys.argv:
        test_real_clock_production()
    print("TESTS RATELIMIT OK")
