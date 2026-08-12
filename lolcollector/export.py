"""Export d'études : JSON consommés par le site EloLab.

`python3 collector.py export --study tierlist [--patch 16.15] --out <dir>`

Sans --patch, le patch courant est détecté via Data Dragon (versions.json) —
jamais de patch codé en dur. Produit dans <dir> :

- tierlist.json : par champion × bucket de rank × région, tous rôles
  confondus — games, wins, winrate, intervalle de confiance à 95 % (Wilson),
  pick_rate, ban_rate, et insufficient_sample=true sous --min-games parties
  (cellule conservée).
- tierlist-roles.json : les mêmes cellules découpées par poste
  (team_position : TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY). Le seuil
  --min-games s'applique à la cellule par rôle, qui est plus petite.
- meta.json : patch, période de collecte, échantillon total et par cellule,
  régions couvertes.

Le rôle vient exclusivement de participants.team_position (renvoyé par
Riot), jamais d'une liste de champions présumée : un Yasuo support joué
1 200 fois est compté en UTILITY, comme il doit l'être.

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
import re
import sqlite3
import time
import urllib.error
import urllib.request

from .db import patch_of

DDRAGON_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"
DDRAGON_CHAMPIONS_URL = "https://ddragon.leagueoflegends.com/cdn/{version}/data/fr_FR/champion.json"

Z_95 = 1.959963984540054  # quantile 97.5 % de la loi normale

DEFAULT_EXPORTS_ROOT = os.path.join("site", "data", "etudes")
PATCH_SLUG_RE = re.compile(r"^\d+-\d+$")


def patch_to_slug(patch: str) -> str:
    """'16.15' -> '16-15' (segment d'URL, et nom de dossier de l'étude)."""
    return patch.replace(".", "-")


def slug_to_patch(slug: str) -> str:
    return slug.replace("-", ".")


def default_out_dir(study: str, patch: str) -> str:
    return os.path.join(DEFAULT_EXPORTS_ROOT, study, patch_to_slug(patch))


class ExportRefused(SystemExit):
    """Refus de garde-fou : rien n'a été écrit, le code de sortie est 1."""

    def __init__(self, message: str):
        super().__init__(f"Export refusé — {message}")

# Index couvrants nécessaires à l'export (et qui accélèrent aussi stats) :
# les requêtes lisent uniquement des colonnes présentes dans l'index,
# aucun aller-retour vers la table.
EXPORT_INDEXES = {
    "idx_matches_export":
        "CREATE INDEX idx_matches_export ON matches"
        " (patch, region, tier_bucket_source, match_id)",
    # team_position fait partie de l'index : sans lui, le GROUP BY par rôle
    # devrait retourner à la table pour 22 M de lignes.
    "idx_participants_export":
        "CREATE INDEX idx_participants_export ON participants"
        " (match_id, champion_id, team_position, win)",
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
    """Crée les index manquants, et RECRÉE ceux dont la définition a changé.

    Sans la comparaison du SQL, un index créé par une version antérieure de
    l'export (par exemple sans team_position) resterait en place : la
    vérification de plan passerait toujours, mais la requête retournerait à
    la table pour chaque ligne. Sur 22 M de participants, c'est la
    différence entre une minute et une heure.
    """
    existing = {
        row[0]: (row[1] or "") for row in conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'index'")
    }

    def normalise(sql: str) -> str:
        return " ".join(sql.split()).replace("( ", "(").replace(" )", ")")

    for name, sql in EXPORT_INDEXES.items():
        if name in existing:
            if normalise(existing[name]) == normalise(sql):
                continue
            print(f"Définition de {name} obsolète, reconstruction…")
            conn.execute(f"DROP INDEX {name}")
        else:
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


# Postes renvoyés par Riot dans team_position. Toute autre valeur (chaîne
# vide sur certains matchs, remakes, modes hors file classée) est comptée
# dans les agrégats tous rôles mais ne produit pas de cellule par poste :
# inventer un rôle serait pire que ne pas en donner.
ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")

# Une seule passe sur participants : les agrégats « tous rôles » sont la
# somme des cellules par rôle, ils ne peuvent donc pas diverger.
PICKS_SQL = (
    "SELECT m.region, m.tier_bucket_source, p.champion_id, p.team_position,"
    " COUNT(*), SUM(p.win)"
    " FROM matches m JOIN participants p ON p.match_id = m.match_id"
    " WHERE m.patch = ?"
    " GROUP BY m.region, m.tier_bucket_source, p.champion_id, p.team_position"
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


def assert_destination_matches(out_dir: str, patch: str) -> None:
    """Interdit d'écrire l'export d'un patch par-dessus celui d'un autre.

    Incident réel : un export de 16.16 (517 matchs, aucune cellule
    exploitable) a écrasé les JSON publiés de 16.15. Deux vérifications
    indépendantes, parce que chacune rattrape ce que l'autre laisse passer :

    1. le dossier de destination porte un slug de patch qui ne correspond
       pas au patch exporté ;
    2. le dossier contient déjà un meta.json d'un autre patch — le cas
       même de l'incident, y compris si le dossier ne porte pas de slug.

    Aucune de ces situations n'est légitime, donc aucune n'est contournable
    par --force : un patch qui n'est pas le bon est toujours une erreur.
    """
    folder = os.path.basename(os.path.normpath(out_dir))
    if PATCH_SLUG_RE.match(folder) and slug_to_patch(folder) != patch:
        raise ExportRefused(
            f"le patch exporté est {patch} mais la destination est « {folder} » "
            f"(patch {slug_to_patch(folder)}).\n"
            f"  Écrire ici écraserait l'étude publiée du patch "
            f"{slug_to_patch(folder)}.\n"
            f"  Destination attendue : {default_out_dir('<étude>', patch)}"
        )

    existing_meta = _read_existing_meta(out_dir)
    existing = existing_meta.get("patch")
    if existing and existing != patch:
        raise ExportRefused(
            f"le patch exporté est {patch} mais "
            f"{os.path.join(out_dir, 'meta.json')} contient déjà l'export du "
            f"patch {existing}.\n"
            f"  Destination attendue : {default_out_dir('<étude>', patch)}"
        )


def _read_existing_meta(out_dir: str) -> dict:
    meta_path = os.path.join(out_dir, "meta.json")
    if not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}  # meta.json illisible : rien à comparer


def assert_not_shrinking(out_dir: str, patch: str, total_matches: int) -> None:
    """Refuse de remplacer un export par un autre nettement plus maigre.

    Le contrôle de patch ne voit pas tout : exporter le BON patch depuis la
    MAUVAISE base (une base de test, une copie tronquée) passe toutes les
    autres vérifications. C'est arrivé — un export de 36 000 matchs
    synthétiques par-dessus les 786 509 matchs publiés du même patch.

    À patch constant, le nombre de matchs ne fait que croître : le collecteur
    accumule. Une chute nette n'est jamais légitime.
    """
    previous = _read_existing_meta(out_dir).get("total_matches")
    if not isinstance(previous, int) or previous <= 0:
        return
    if total_matches >= previous // 2:
        return
    raise ExportRefused(
        f"l'export ne contient que {total_matches:,} matchs alors que "
        f"{out_dir} en publie déjà {previous:,} pour le même patch "
        f"{patch}.".replace(",", " ") + "\n"
        f"  À patch constant le volume ne fait que croître : c'est "
        f"probablement la mauvaise base (DB_PATH).\n"
        f"  Pour écrire quand même : --force."
    )


def _write_json(path: str, payload, compact: bool = False) -> None:
    """Écriture atomique : un export interrompu ne laisse pas de JSON tronqué
    à la place d'une étude publiée."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def export_tierlist(db_path: str, patch: str | None, out_dir: str | None,
                    min_games: int = 200, force: bool = False,
                    study: str = "tierlist") -> None:
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

        # Le chemin par défaut se déduit du patch : la destination ne peut
        # pas se retrouver en désaccord avec ce qu'on exporte.
        if out_dir is None:
            out_dir = default_out_dir(study, patch)
            print(f"Destination déduite du patch : {out_dir}")
        # Garde-fou AVANT les requêtes lourdes : échouer en une seconde
        # plutôt qu'après plusieurs minutes de GROUP BY.
        assert_destination_matches(out_dir, patch)

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

        # picks_by_role : (champ, region, bucket, role) -> (games, wins)
        # picks         : (champ, region, bucket)       -> (games, wins),
        #                 somme de TOUTES les valeurs de team_position, y
        #                 compris celles hors des cinq postes connus.
        picks_by_role: dict[tuple, tuple[int, int]] = {}
        picks: dict[tuple, tuple[int, int]] = {}
        for region, bucket, champ, position, games, wins in conn.execute(
                PICKS_SQL, (patch,)):
            wins = wins or 0
            total = picks.get((champ, region, bucket), (0, 0))
            picks[(champ, region, bucket)] = (total[0] + games, total[1] + wins)
            if position in ROLES:
                picks_by_role[(champ, region, bucket, position)] = (games, wins)
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

        # Cellules par poste, volontairement réduites aux compteurs bruts.
        #
        # Ce fichier a cinq fois plus de lignes que tierlist.json et il part
        # dans la page servie au lecteur : chaque champ compte. Or winrate,
        # bornes de Wilson, pick rate et insufficient_sample sont tous
        # dérivables de (games, wins, matchs de la cellule, min_cell_games) —
        # et de fait, ni le tableau du site ni verify_study.py ne lisent les
        # champs dérivés de tierlist.json : les deux les recalculent avec la
        # même formule. Les stocker ici les rendrait juste plus gros.
        #
        # Pas de champion_name non plus (jointure par champion_id sur le
        # fichier principal), et surtout pas de bans : un ban vise un
        # champion pour toute la partie, il n'a pas de poste. Écrire 0
        # laisserait croire que personne ne bannit ce champion à ce poste.
        role_rows = [
            {
                "champion_id": champ,
                "region": region,
                "bucket": bucket,
                "role": role,
                "games": picks_by_role[(champ, region, bucket, role)][0],
                "wins": picks_by_role[(champ, region, bucket, role)][1],
            }
            for champ, region, bucket, role in sorted(picks_by_role)
        ]

        first, last = conn.execute(
            "SELECT MIN(inserted_at), MAX(inserted_at) FROM matches WHERE patch = ?",
            (patch,),
        ).fetchone()

        # Second garde-fou : un export sans une seule cellule au-dessus du
        # seuil n'est pas une étude, c'est un écrasement. C'est exactement
        # ce qui s'est produit avec 517 matchs de 16.16.
        if not force:
            assert_not_shrinking(out_dir, patch, sum(cells.values()))

        usable = sum(1 for r in rows if not r["insufficient_sample"])
        if usable == 0 and not force:
            raise ExportRefused(
                f"aucune cellule n'atteint {min_games} games sur le patch "
                f"{patch} ({sum(cells.values())} matchs collectés, "
                f"{len(rows)} cellules toutes sous le seuil).\n"
                f"  La collecte du patch vient probablement de commencer. "
                f"Rien n'a été écrit dans {out_dir}.\n"
                f"  Pour écrire quand même : --force."
            )
        if usable == 0:
            print(f"! --force : export écrit sans aucune cellule à "
                  f"{min_games} games.")

        os.makedirs(out_dir, exist_ok=True)
        _write_json(os.path.join(out_dir, "tierlist.json"), rows)
        # Compact (pas d'indent) : ce fichier est cinq fois plus gros et il
        # part dans la page servie au lecteur.
        _write_json(os.path.join(out_dir, "tierlist-roles.json"), role_rows,
                    compact=True)
        meta = {
            "study": "tierlist",
            "patch": patch,
            "ddragon_version": ddragon_version,
            "exported_at": time.strftime("%Y-%m-%d", time.gmtime()),
            "collected_from": time.strftime("%Y-%m-%d", time.gmtime(first)) if first else None,
            "collected_to": time.strftime("%Y-%m-%d", time.gmtime(last)) if last else None,
            "total_matches": sum(cells.values()),
            "regions": sorted({region for region, _ in cells}),
            "roles": list(ROLES),
            "role_cells": len(role_rows),
            "min_cell_games": min_games,
            "total_cells": len(rows),
            "usable_cells": usable,
            "cells": [
                {"region": region, "bucket": bucket, "matches": count}
                for (region, bucket), count in sorted(cells.items())
            ],
        }
        _write_json(os.path.join(out_dir, "meta.json"), meta)

        insufficient = sum(1 for r in rows if r["insufficient_sample"])
        role_insufficient = sum(1 for r in role_rows if r["games"] < min_games)
        print(f"Export tierlist {patch} : {len(rows)} cellules tous rôles "
              f"({insufficient} sous {min_games} games), "
              f"{len(role_rows)} cellules par poste "
              f"({role_insufficient} sous le seuil), "
              f"{meta['total_matches']} matchs, {time.time() - started:.1f}s"
              f" -> {out_dir}/tierlist.json + tierlist-roles.json + meta.json")
        return {**meta, "out_dir": out_dir}
    finally:
        conn.close()
