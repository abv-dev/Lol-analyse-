"""Échantillonnage de joueurs via League-Exp-V4, avec curseur persisté par région/bucket."""

import random

from .config import APEX_TIERS, DIVISIONS


class BucketSampler:
    """Itère les joueurs d'un bucket de tiers (ex: SILVER+GOLD) sur une plateforme.

    Le curseur (tier_idx, div_idx, page) est persisté en base après chaque page
    League-Exp consommée, pour reprendre au même endroit après un crash.
    Quand tout le bucket a été parcouru, on repart du début (le ladder ayant
    bougé entre-temps, on ré-échantillonne des joueurs frais).
    """

    def __init__(self, db, client, region, platform, bucket, tiers, max_pages, logger):
        self.db = db
        self.client = client
        self.region = region
        self.platform = platform
        self.bucket = bucket
        self.tiers = tiers
        self.max_pages = max_pages
        self.log = logger
        cursor = db.load_cursor(region, bucket)
        self.tier_idx, self.div_idx, self.page = cursor if cursor else (0, 0, 1)
        self._clamp_cursor()
        self._buffer = []  # puuids de la dernière page, consommés un par un

    def _divisions_for(self, tier: str):
        return ["I"] if tier in APEX_TIERS else DIVISIONS

    def _clamp_cursor(self):
        if self.tier_idx >= len(self.tiers):
            self.tier_idx, self.div_idx, self.page = 0, 0, 1
        tier = self.tiers[self.tier_idx]
        if self.div_idx >= len(self._divisions_for(tier)):
            self.div_idx, self.page = 0, 1

    def _advance(self, page_was_empty: bool):
        if not page_was_empty and self.page < self.max_pages:
            self.page += 1
            return
        # division suivante, sinon tier suivant, sinon retour au début du bucket
        self.page = 1
        self.div_idx += 1
        tier = self.tiers[self.tier_idx]
        if self.div_idx >= len(self._divisions_for(tier)):
            self.div_idx = 0
            self.tier_idx = (self.tier_idx + 1) % len(self.tiers)

    async def next_puuid(self):
        """Rend le prochain puuid du bucket, ou None si aucun joueur trouvable."""
        if self._buffer:
            return self._buffer.pop()

        # Au plus un tour complet du bucket avant d'abandonner ce cycle
        max_fetches = sum(len(self._divisions_for(t)) for t in self.tiers) + 1
        for _ in range(max_fetches):
            tier = self.tiers[self.tier_idx]
            division = self._divisions_for(tier)[self.div_idx]
            entries = await self.client.league_entries(
                self.platform, tier, division, self.page
            )
            self._advance(page_was_empty=not entries)
            self.db.save_cursor(self.region, self.bucket,
                                self.tier_idx, self.div_idx, self.page)
            if entries:
                puuids = [e["puuid"] for e in entries if e.get("puuid")]
                random.shuffle(puuids)
                self._buffer = puuids
                if self._buffer:
                    return self._buffer.pop()
        self.log.warning("[%s/%s] aucun joueur trouvé sur un tour complet du bucket",
                         self.region, self.bucket)
        return None
