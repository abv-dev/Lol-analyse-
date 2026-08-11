"""Purge des matchs bruts des vieux patchs.

    python3 collector.py prune --keep-patches 2 [--exports <dir>] [--yes]

Les études publiées ne dépendent QUE des JSON exportés
(`site/data/etudes/<famille>/<patch-slug>/`), jamais de la base : une fois un
patch exporté, ses matchs bruts ne servent plus qu'à ré-exporter. La purge
libère donc de l'espace sans rien casser côté site.

Garde-fou : un patch n'est supprimé que si un **export agrégé existe** pour
lui. Sans export, on refuse et on l'indique — supprimer serait une perte
définitive.
"""

import os
import sqlite3

from .db import patch_of

DEFAULT_EXPORTS_DIR = os.path.join("site", "data", "etudes")


def patch_slug(patch: str) -> str:
    return patch.replace(".", "-")


def exported_patches(exports_dir: str) -> set[str]:
    """Patchs pour lesquels un export agrégé existe (toutes familles)."""
    found: set[str] = set()
    if not os.path.isdir(exports_dir):
        return found
    for family in os.listdir(exports_dir):
        family_dir = os.path.join(exports_dir, family)
        if not os.path.isdir(family_dir):
            continue
        for slug in os.listdir(family_dir):
            study_dir = os.path.join(family_dir, slug)
            if os.path.isfile(os.path.join(study_dir, "meta.json")):
                found.add(slug.replace("-", "."))
    return found


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def run_prune(db_path: str, keep_patches: int, exports_dir: str,
              assume_yes: bool = False) -> int:
    if not os.path.exists(db_path):
        print(f"Base introuvable : {db_path}")
        return 1
    if keep_patches < 1:
        print("--keep-patches doit valoir au moins 1.")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        patches = [
            p for (p,) in conn.execute(
                "SELECT DISTINCT patch FROM matches WHERE patch IS NOT NULL"
                " ORDER BY patch DESC")
        ]
        if len(patches) <= keep_patches:
            print(f"{len(patches)} patch(s) en base, {keep_patches} à conserver : "
                  "rien à purger.")
            return 0

        kept, candidates = patches[:keep_patches], patches[keep_patches:]
        exported = exported_patches(exports_dir)
        to_purge = [p for p in candidates if p in exported]
        blocked = [p for p in candidates if p not in exported]

        print(f"Patchs conservés  : {', '.join(kept)}")
        if blocked:
            print(f"Patchs NON purgés : {', '.join(blocked)} — aucun export agrégé "
                  f"trouvé dans {exports_dir}. Exporter d'abord "
                  "(collector.py export --study tierlist --patch X.Y --out …).")
        if not to_purge:
            print("Aucun patch purgeable (tous ceux au-delà de la fenêtre "
                  "manquent d'export).")
            return 0

        counts = {}
        for patch in to_purge:
            counts[patch] = conn.execute(
                "SELECT COUNT(*) FROM matches WHERE patch = ?", (patch,)).fetchone()[0]
        total = sum(counts.values())
        print(f"Patchs à purger   : "
              + ", ".join(f"{p} ({counts[p]} matchs)" for p in to_purge))
        print(f"Total             : {total} matchs bruts + leurs participants, "
              "bans, objectifs et timelines")

        if not assume_yes:
            answer = input("Confirmer la suppression ? [oui/NON] ").strip().lower()
            if answer not in ("oui", "o", "yes", "y"):
                print("Annulé, rien n'a été supprimé.")
                return 0

        cur = conn.cursor()
        deleted = {}
        for patch in to_purge:
            cur.execute("BEGIN")
            cur.execute(
                "CREATE TEMP TABLE IF NOT EXISTS _purge_ids (match_id TEXT PRIMARY KEY)")
            cur.execute("DELETE FROM _purge_ids")
            cur.execute("INSERT INTO _purge_ids SELECT match_id FROM matches"
                        " WHERE patch = ?", (patch,))
            for table in ("participants", "bans", "team_objectives",
                          "timeline_events", "timeline_frames", "timeline_state"):
                if _table_exists(conn, table):
                    cur.execute(f"DELETE FROM {table} WHERE match_id IN"
                                " (SELECT match_id FROM _purge_ids)")
            cur.execute("DELETE FROM matches WHERE patch = ?", (patch,))
            deleted[patch] = cur.rowcount
            conn.commit()
            print(f"  {patch} : {deleted[patch]} matchs supprimés")

        print("Compactage de la base (VACUUM), cela peut prendre plusieurs "
              "minutes sur une grosse base…")
        size_before = os.path.getsize(db_path)
        conn.execute("VACUUM")
        size_after = os.path.getsize(db_path)
        freed = (size_before - size_after) / 1024 ** 3
        print(f"Terminé : {sum(deleted.values())} matchs purgés, "
              f"{freed:.2f} Go libérés "
              f"({size_before / 1024**3:.2f} -> {size_after / 1024**3:.2f} Go)")
        return 0
    finally:
        conn.close()
