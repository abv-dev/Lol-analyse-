"""Export d'études : JSON consommés par le site EloLab.

`python3 collector.py export --study tierlist [--patch 16.15] --out <dir>`

Sans --patch, le patch courant est détecté via Data Dragon (versions.json) —
jamais de patch codé en dur. Produit dans <dir> :

- tierlist.json : par champion × bucket de rank × région — games, wins,
  winrate, intervalle de confiance à 95 % (Wilson), pick_rate, ban_rate,
  et insufficient_sample=true sous --min-games parties (cellule conservée).
- meta.json : patch, période de collecte, échantillon total et par cellule,
  régions couvertes.

Les noms de champions viennent de Data Dragon (id -> nom) pour la version
du patch exporté, avec repli sur les noms stockés en base si ddragon est
injoignable.

Performance : toutes les requêtes lourdes s'appuient sur des index couvrants
(créés au besoin, une seule fois) — vérifié via EXPLAIN QUERY PLAN à chaque
export. Aucun scan de table sur matches/participants/bans.
"""

import json
import math
import os
import sqlite3
import time
import urllib.error
import urllib.request

from .db import patch_of

DDRAGON_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_CHAMPIONS_URL = "https://ddragon.leagueoflegends.com/cdn/{version}/data/fr_FR/champion.json"

Z_95 = 1.959963984540054  # quantile 97.5 % de la loi normale

# Index couvrants nécessaires à l'export (et qui accélèrent aussi stats) :
# les requêtes lisent uniquement des colonnes présentes dans l'index,
# aucun aller-retour vers la table.
EXPORT_INDEXES = {
    "idx_matches_export":
        "CREATE INDEX idx_matches_export ON matches"
        " (patch, region, tier_bucket_source, match_id)",
    "idx_participants_export":
        "CREATE INDEX idx_participants_export ON participants"
        " (match_id, champion_id, win)",
    "idx_bans_export":
        "CREATE INDEX idx_bans_export ON bans (match_id, champion_id)",
}


def wilson_ci(wins: int, games: int, z: float = Z_95) -> tuple[float, float]:
    """Intervalle de confiance de Wilson à 95 % sur une proportion."""
    if games == 0:
        return (0.0, 0.0)
    phat = wins / games
    denom = 1 + z * z / games
    centre = phat + z * z / (2 * games)
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * games)) / games)
    return ((centre - margin) / denom, (centre + margin) / denom)


def _fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def detect_patch_and_version(patch: str | None):
    """Retourne (patch, version ddragon complète pour ce patch).

    Sans patch demandé : le patch courant = deux premiers segments de la
    dernière version de versions.json. La version complète sert à charger
    les noms de champions du bon patch."""
    try:
        versions = _fetch_json(DDRAGON_VERSIONS_URL)
    except (urllib.error.URLError, OSError) as exc:
        print(f"! Data Dragon injoignable ({exc})")
        return patch, None
    if patch is None:
        patch = patch_of(versions[0])
        print(f"Patch courant détecté via Data Dragon : {patch}")
    full = next((v for v in versions if patch_of(v) == patch), None)
    return patch, full


def fetch_champion_names(version: str | None) -> dict[int, str]:
    """id numérique -> nom affichable, depuis champion.json de la version."""
    if not version:
        return {}
    try:
        data = _fetch_json(DDRAGON_CHAMPIONS_URL.format(version=version))
        return {int(c["key"]): c["name"] for c in data["data"].values()}
    except (urllib.error.URLError, OSError, KeyError, ValueError) as exc:
        print(f"! champion.json {version} injoignable ({exc}), "
              "repli sur les noms stockés en base")
        return {}


def ensure_export_indexes(conn: sqlite3.Connection) -> None:
    existing = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    for name, sql in EXPORT_INDEXES.items():
        if name not in existing:
            print(f"Création de l'index {name} (une seule fois, patiente)…")
            conn.execute(sql)
    conn.commit()


def _assert_covering_plan(conn: sqlite3.Connection, sql: str, params) -> None:
    """Garde-fou perf : la requête doit passer par des index, aucun
    « SCAN <table> » nu sur les grosses tables."""
    plan = "\n".join(
        row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)
    )
    for table in ("matches", "participants", "bans"):
        if f"SCAN {table}" in plan and "USING" not in plan:
            raise RuntimeError(f"Plan de requête sans index sur {table} :\n{plan}")
    if "USING" not in plan:
        raise RuntimeError(f"Plan de requête inattendu :\n{plan}")


PICKS_SQL = (
    "SELECT m.region, m.tier_bucket_source, p.champion_id, COUNT(*), SUM(p.win)"
    " FROM matches m JOIN participants p ON p.match_id = m.match_id"
    " WHERE m.patch = ?"
    " GROUP BY m.region, m.tier_bucket_source, p.champion_id"
)
BANS_SQL = (
    "SELECT m.region, m.tier_bucket_source, b.champion_id, COUNT(*)"
    " FROM matches m JOIN bans b ON b.match_id = m.match_id"
    " WHERE m.patch = ? AND b.champion_id > 0"
    " GROUP BY m.region, m.tier_bucket_source, b.champion_id"
)
CELLS_SQL = (
    "SELECT region, tier_bucket_source, COUNT(*) FROM matches"
    " WHERE patch = ? GROUP BY region, tier_bucket_source"
)


def export_tierlist(db_path: str, patch: str | None, out_dir: str,
                    min_games: int = 200) -> None:
    if not os.path.exists(db_path):
        raise SystemExit(f"Base introuvable : {db_path}")
    started = time.time()
    conn = sqlite3.connect(db_path)
    try:
        patch, ddragon_version = detect_patch_and_version(patch)
        if patch is None:
            # ddragon injoignable ET pas de --patch : repli sur la version vue
            # par le collecteur (toujours pas de patch codé en dur)
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'ddragon_current'").fetchone()
            if not row:
                raise SystemExit(
                    "Impossible de détecter le patch (ddragon injoignable et "
                    "meta vide) — précise --patch X.Y")
            patch = patch_of(row[0])
            print(f"Patch courant repris de la base : {patch}")

        ensure_export_indexes(conn)
        for sql in (PICKS_SQL, BANS_SQL, CELLS_SQL):
            _assert_covering_plan(conn, sql, (patch,))
        print("Plans de requête vérifiés : index couvrants utilisés.")

        cells = {
            (region, bucket): count
            for region, bucket, count in conn.execute(CELLS_SQL, (patch,))
        }
        if not cells:
            raise SystemExit(f"Aucun match en base pour le patch {patch}")

        picks = {
            (champ, region, bucket): (games, wins or 0)
            for region, bucket, champ, games, wins in conn.execute(PICKS_SQL, (patch,))
        }
        bans = {
            (champ, region, bucket): count
            for region, bucket, champ, count in conn.execute(BANS_SQL, (patch,))
        }

        names = fetch_champion_names(ddragon_version)

        def resolve_name(champ_id: int) -> str:
            if champ_id in names:
                return names[champ_id]
            row = conn.execute(
                "SELECT champion_name FROM participants"
                " WHERE champion_id = ? AND patch = ? LIMIT 1",
                (champ_id, patch),
            ).fetchone()
            return row[0] if row and row[0] else str(champ_id)

        rows = []
        for champ, region, bucket in sorted(set(picks) | set(bans)):
            games, wins = picks.get((champ, region, bucket), (0, 0))
            ban_count = bans.get((champ, region, bucket), 0)
            cell_matches = cells.get((region, bucket), 0)
            ci_low, ci_high = wilson_ci(wins, games)
            rows.append({
                "champion_id": champ,
                "champion_name": resolve_name(champ),
                "region": region,
                "bucket": bucket,
                "games": games,
                "wins": wins,
                "winrate": round(wins / games, 4) if games else None,
                "winrate_ci_low": round(ci_low, 4) if games else None,
                "winrate_ci_high": round(ci_high, 4) if games else None,
                "pick_rate": round(games / cell_matches, 4) if cell_matches else None,
                "ban_rate": round(ban_count / cell_matches, 4) if cell_matches else None,
                "bans": ban_count,
                "insufficient_sample": games < min_games,
            })

        first, last = conn.execute(
            "SELECT MIN(inserted_at), MAX(inserted_at) FROM matches WHERE patch = ?",
            (patch,),
        ).fetchone()

        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, "tierlist.json"), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        meta = {
            "study": "tierlist",
            "patch": patch,
            "ddragon_version": ddragon_version,
            "exported_at": time.strftime("%Y-%m-%d", time.gmtime()),
            "collected_from": time.strftime("%Y-%m-%d", time.gmtime(first)) if first else None,
            "collected_to": time.strftime("%Y-%m-%d", time.gmtime(last)) if last else None,
            "total_matches": sum(cells.values()),
            "regions": sorted({region for region, _ in cells}),
            "min_cell_games": min_games,
            "cells": [
                {"region": region, "bucket": bucket, "matches": count}
                for (region, bucket), count in sorted(cells.items())
            ],
        }
        with open(os.path.join(out_dir, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=1)

        insufficient = sum(1 for r in rows if r["insufficient_sample"])
        print(f"Export tierlist {patch} : {len(rows)} cellules "
              f"({insufficient} sous {min_games} games), "
              f"{meta['total_matches']} matchs, {time.time() - started:.1f}s"
              f" -> {out_dir}/tierlist.json + meta.json")
    finally:
        conn.close()
