"""Liste des objets légendaires complétés, par patch (spec Lot 14, §3.1).

Un « légendaire complété » est un objet qu'un joueur peut acheter et qui ne
s'améliore pas ensuite : c'est ce que mesure la métrique « timestamp du 1er
item légendaire complété ». Le filtre se lit sur `item.json` de Data Dragon :

    achetable (`gold.purchasable`), sans amélioration (`into` vide),
    `gold.total >= 2000`, disponible sur la carte 11 (Faille de l'invocateur).

La liste retenue est stockée dans `legendary_items` avec la version Data
Dragon dans `meta` : une étude aval reconstitue exactement le filtre appliqué
le jour de la collecte, sans dépendre de ce que Data Dragon sert aujourd'hui.

Invariant : ce module ne bloque JAMAIS la collecte de timelines. Patch inconnu
et réseau tombé -> repli sur la liste du patch précédent, journalisé, nouvelle
tentative plus tard ; à défaut, ensemble vide et la timeline est stockée sans
ses item_events.
"""

import logging
import time

from .db import patch_of

MIN_GOLD = 2000            # seuil « légendaire » du §3.1
SUMMONERS_RIFT = "11"      # maps["11"] : Faille de l'invocateur
RETRY_INTERVAL = 900       # 15 min entre deux tentatives ddragon après échec


def is_legendary(item: dict) -> bool:
    """Filtre du §3.1 sur une entrée de `item.json`."""
    gold = item.get("gold") or {}
    maps = item.get("maps") or {}
    return (bool(gold.get("purchasable"))
            and not (item.get("into") or [])
            and (gold.get("total") or 0) >= MIN_GOLD
            and bool(maps.get(SUMMONERS_RIFT)))


def legendary_ids(payload: dict) -> set[int]:
    """Ids des légendaires complétés d'un `item.json` Data Dragon."""
    ids = set()
    for key, item in (payload.get("data") or {}).items():
        if not is_legendary(item):
            continue
        try:
            ids.add(int(key))
        except (TypeError, ValueError):
            continue
    return ids


def meta_key(patch: str) -> str:
    return f"legendary_ddragon_{patch}"


def store_legendary_items(db, patch: str, ids, version: str | None = None) -> None:
    """Enregistre la liste d'un patch (idempotent) et sa version ddragon."""
    db.conn.executemany(
        "INSERT OR IGNORE INTO legendary_items (patch, item_id) VALUES (?,?)",
        [(patch, int(item_id)) for item_id in ids],
    )
    db.conn.commit()
    if version:
        db.set_meta(meta_key(patch), version)


def load_legendary_items(db, patch: str) -> set[int]:
    """Liste stockée pour ce patch exactement (ensemble vide si absente)."""
    return {row[0] for row in db.conn.execute(
        "SELECT item_id FROM legendary_items WHERE patch = ?", (patch,))}


def legendary_for_patch(db, patch: str, log: logging.Logger | None = None
                        ) -> tuple[set[int], str | None]:
    """Liste applicable à un patch, avec repli sur le patch précédent.

    Retourne (ids, patch source). Le patch source vaut `patch` en régime
    normal, le patch antérieur le plus récent en repli, None si la base n'a
    aucune liste.
    """
    ids = load_legendary_items(db, patch)
    if ids:
        return ids, patch

    # Tri numérique : « 16.9 » est ANTÉRIEUR à « 16.15 », le tri de SQLite dit
    # l'inverse.
    from .db import patch_sort_key

    cible = patch_sort_key(patch)
    precedents = sorted(
        (p for (p,) in db.conn.execute("SELECT DISTINCT patch FROM legendary_items")
         if patch_sort_key(p) < cible),
        key=patch_sort_key,
    )
    if not precedents:
        return set(), None
    source = precedents[-1]
    if log:
        log.warning("objets légendaires : aucune liste pour le patch %s, repli "
                    "sur celle du patch %s", patch, source)
    return load_legendary_items(db, source), source


class LegendaryItems:
    """Liste courante par patch : cache mémoire, chargement ddragon, repli.

    `ids_for` est appelé sur le chemin de stockage d'une timeline (worker et
    backfill-timelines) : il ne fait de requête réseau qu'au premier match d'un
    patch inconnu, et au plus une fois par `retry_interval` après un échec.
    """

    def __init__(self, db, log: logging.Logger | None = None,
                 retry_interval: int = RETRY_INTERVAL):
        self.db = db
        self.log = log or logging.getLogger("collector")
        self.retry_interval = retry_interval
        self._cache: dict[str, set[int]] = {}
        self._next_try = 0.0
        self._repli_journalise: set[str] = set()

    async def ids_for(self, client, patch: str) -> set[int]:
        if not patch:
            return set()
        connu = self._cache.get(patch)
        if connu is not None:
            return connu

        ids = load_legendary_items(self.db, patch)
        if ids:
            self._cache[patch] = ids
            return ids

        if time.time() >= self._next_try:
            ids = await self._charger(client, patch)
            if ids:
                self._cache[patch] = ids
                return ids

        # Repli : journalisé une fois par patch, la collecte continue.
        log = self.log if patch not in self._repli_journalise else None
        self._repli_journalise.add(patch)
        ids, _source = legendary_for_patch(self.db, patch, log)
        return ids

    async def _charger(self, client, patch: str) -> set[int]:
        """Récupère item.json pour ce patch et le stocke. Ensemble vide si le
        réseau ou Data Dragon fait défaut (nouvelle tentative plus tard)."""
        try:
            versions = await client.ddragon_versions() or []
            version = next((v for v in versions if patch_of(v) == patch), None)
            if version is None:
                raise LookupError(
                    f"aucune version Data Dragon pour le patch {patch}")
            payload = await client.ddragon_items(version)
            ids = legendary_ids(payload or {})
            if not ids:
                raise ValueError(f"item.json {version} : aucun objet légendaire")
        except Exception as exc:            # réseau, 404, payload inattendu
            self._next_try = time.time() + self.retry_interval
            self.log.warning("objets légendaires : chargement du patch %s "
                             "échoué (%s), nouvelle tentative dans %d s",
                             patch, exc, self.retry_interval)
            return set()
        store_legendary_items(self.db, patch, ids, version)
        self.log.info("objets légendaires : %d objets retenus pour le patch %s "
                      "(Data Dragon %s)", len(ids), patch, version)
        return ids
