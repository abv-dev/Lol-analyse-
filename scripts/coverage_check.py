#!/usr/bin/env python3
"""Lot 0 — mesure de couverture du module Coach.

Compte, cellule par cellule (`champion × team_position × bucket × région`),
les effectifs réellement disponibles dans `data/matches.db`. Le script ne
décide rien : il compte et publie. Les seuils de validité et le sort des
métriques Timeline se tranchent **après** lecture de `out/coverage/`.

Il est prévu pour tourner **pendant que le collecteur écrit** :

- ouverture en `mode=ro` + `PRAGMA query_only=ON` (aucune écriture possible,
  pas d'`ATTACH`, pas de `CREATE`, même `TEMP`) ;
- `PRAGMA busy_timeout=5000` et autocommit (aucune transaction longue) ;
- une requête par champion × patch, toutes passant par
  `idx_participants_champ_patch` : chaque requête est précédée d'un
  `EXPLAIN QUERY PLAN` affiché, et **tout `SCAN` de `participants` (ou de ses
  alias `p`/`p2`) arrête le script en code 2 avant de l'exécuter**.

Stdlib uniquement, aucune dépendance à `lolcollector`.

Usage :
    python3 scripts/coverage_check.py [--patch 16.16 | --all-patches]
                                      [--db data/matches.db] [--out out/coverage]

Codes de sortie : 0 succès · 1 erreur (patch absent, garde-fou champion, base
introuvable) · 2 plan d'exécution avec `SCAN` de `participants`.
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# --- constantes du §3 de la spec (relevées sur la base réelle) --------------

CHAMPIONS = {"Kaisa": 145, "Galio": 3, "Nilah": 895}
POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
BUCKETS = ("IRON_BRONZE", "SILVER_GOLD", "PLAT_EMERALD", "DIAMOND_PLUS")
REGIONS = ("europe", "asia", "americas")
ALL_REGIONS = "ALL"

MIN_DURATION_S = 1200          # 20 minutes, en secondes
MIN_GAMES_PER_PUUID = 5        # seuil de la colonne n_puuid_ge5

FILTERS = {
    "min_duration_s": MIN_DURATION_S,
    "early_surrender": "couvert par min_duration_s (aucune colonne dédiée)",
    "afk_proxy": ("exclusion si un participant a kills=0 AND assists=0"
                  " AND total_cs=0"),
    "clean_scope": "les compteurs 3-5 portent sur les games propres",
}

EXIT_OK, EXIT_ERROR, EXIT_SCAN = 0, 1, 2

# Une ligne de plan qui commence par SCAN et porte sur participants ou l'un de
# ses alias imposés (p, p2) : chemin d'accès interdit.
SCAN_RE = re.compile(r"^SCAN\s+(?:participants|p2?)\b")

# Requête de couverture — §5 : les chemins d'accès sont imposés.
SQL_COVERAGE = """
SELECT p.match_id, p.puuid, p.team_position,
       m.region, m.tier_bucket_source, m.game_duration,
       (ts.match_id IS NOT NULL) AS has_timeline,
       NOT EXISTS (
         SELECT 1 FROM participants p2
         WHERE p2.match_id = p.match_id
           AND p2.kills = 0 AND p2.assists = 0 AND p2.total_cs = 0
       ) AS no_afk
FROM participants p
JOIN matches m ON m.match_id = p.match_id
LEFT JOIN timeline_state ts
       ON ts.match_id = p.match_id AND ts.status = 'ok'
WHERE p.champion_id = ? AND p.patch = ?
"""

SQL_PATCHES = "SELECT DISTINCT patch FROM matches"
SQL_CHAMPION_NAME = ("SELECT champion_name FROM participants"
                     " WHERE champion_id = ? LIMIT 1")


class CoverageExit(Exception):
    """Erreur fatale portant son code de sortie."""

    code = EXIT_ERROR

    def __init__(self, message, code=EXIT_ERROR):
        super().__init__(message)
        self.code = code


class Fatal(CoverageExit):
    """Erreur d'usage ou de données : code 1."""

    def __init__(self, message):
        super().__init__(message, EXIT_ERROR)


class ScanDetected(CoverageExit):
    """Plan d'exécution scannant `participants` : code 2, rien n'est exécuté."""

    def __init__(self, message):
        super().__init__(message, EXIT_SCAN)


# --- accès base -------------------------------------------------------------

def open_db(db_path):
    """Ouvre la base en lecture seule. Le collecteur écrit pendant ce temps."""
    if not Path(db_path).exists():
        raise Fatal(f"base introuvable : {db_path}")
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.OperationalError as exc:
        raise Fatal(f"base illisible : {db_path} ({exc})") from exc
    conn.execute("PRAGMA query_only=ON;")
    conn.execute("PRAGMA busy_timeout=5000;")
    return conn


def check_plan(conn, sql, params=(), label=""):
    """Affiche l'EXPLAIN QUERY PLAN et refuse tout SCAN de `participants`.

    Retourne les lignes du plan. Lève `ScanDetected` (code 2) **avant** que la
    requête ne soit exécutée.
    """
    plan = [row[3].strip()
            for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)]
    print(f"EXPLAIN QUERY PLAN — {label or 'requête'}")
    for line in plan:
        print(f"    {line}")
    for line in plan:
        if SCAN_RE.match(line):
            raise ScanDetected(
                f"plan interdit sur « {label or 'requête'} » : {line}\n"
                "   `participants` (22 M lignes) doit être atteinte par index ;"
                " requête non exécutée.")
    return plan


def run_query(conn, sql, params=(), label=""):
    """`check_plan` puis exécution — jamais l'inverse."""
    check_plan(conn, sql, params, label)
    return conn.execute(sql, params)


# --- patchs -----------------------------------------------------------------

def patch_sort_key(patch):
    """Tri version-aware : (16, 9) < (16, 16). Le tri lexical est faux."""
    return tuple(int(part) if part.isdigit() else -1
                 for part in str(patch).split("."))


def list_patches(conn):
    rows = run_query(conn, SQL_PATCHES, label="liste des patchs")
    patches = [row[0] for row in rows if row[0]]
    return sorted(patches, key=patch_sort_key)


# --- garde-fou champion -----------------------------------------------------

def check_champions(conn):
    """Refuse de compter si un champion_id ne porte pas le nom attendu.

    Un id absent de la base ne contredit rien : les cellules du champion
    sortiront à zéro, ce qui est l'information recherchée.
    """
    for name, champion_id in CHAMPIONS.items():
        row = run_query(conn, SQL_CHAMPION_NAME, (champion_id,),
                        label=f"garde-fou {name} ({champion_id})").fetchone()
        if row is None:
            print(f"    (aucune ligne pour champion_id={champion_id} :"
                  f" {name} sortira à zéro)")
            continue
        if row[0] != name:
            raise Fatal(f"garde-fou champion : champion_id={champion_id} porte"
                        f" le nom « {row[0]} » en base, « {name} » attendu.")


# --- comptage ---------------------------------------------------------------

def _blank_cell():
    return {"n_games": 0, "n_clean": 0, "n_timeline": 0, "puuids": Counter()}


def collect(conn, patch):
    """Compte les cellules du patch. Retourne (rows, exclusions)."""
    cells = {}
    excluded_unknown_position = 0
    excluded_null_puuid = 0
    excluded_unknown_scope = 0

    for champion, champion_id in CHAMPIONS.items():
        rows = run_query(conn, SQL_COVERAGE, (champion_id, patch),
                         label=f"couverture {champion} ({champion_id}) — {patch}")
        for (_match_id, puuid, position, region, bucket, duration,
             has_timeline, no_afk) in rows:
            if position not in POSITIONS:
                excluded_unknown_position += 1
                continue
            if region not in REGIONS or bucket not in BUCKETS:
                # §6 fige les régions et les buckets ; une valeur hors liste
                # ne peut être placée nulle part. Zéro cas attendu en base.
                excluded_unknown_scope += 1
                continue

            clean = duration is not None and duration >= MIN_DURATION_S and no_afk
            if clean and puuid is None:
                excluded_null_puuid += 1

            for scope in (region, ALL_REGIONS):
                cell = cells.setdefault((champion, position, bucket, scope),
                                        _blank_cell())
                cell["n_games"] += 1
                if not clean:
                    continue
                cell["n_clean"] += 1
                if has_timeline:
                    cell["n_timeline"] += 1
                if puuid is not None:
                    cell["puuids"][puuid] += 1

    out_rows = []
    for champion, champion_id in CHAMPIONS.items():
        for position in POSITIONS:
            for bucket in BUCKETS:
                for region in REGIONS + (ALL_REGIONS,):
                    cell = cells.get((champion, position, bucket, region),
                                     _blank_cell())
                    puuids = cell["puuids"]
                    out_rows.append({
                        "champion": champion,
                        "champion_id": champion_id,
                        "team_position": position,
                        "bucket": bucket,
                        "region": region,
                        "n_games": cell["n_games"],
                        "n_clean": cell["n_clean"],
                        "n_puuid": len(puuids),
                        "n_puuid_ge5": sum(1 for n in puuids.values()
                                           if n >= MIN_GAMES_PER_PUUID),
                        "n_timeline": cell["n_timeline"],
                    })

    return out_rows, {
        "excluded_unknown_position": excluded_unknown_position,
        "excluded_null_puuid": excluded_null_puuid,
        "excluded_unknown_scope": excluded_unknown_scope,
    }


# --- sorties ----------------------------------------------------------------

HEADERS = ("position", "bucket", "région", "games", "propres", "joueurs",
           "joueurs≥5", "timelines")
FIELDS = ("team_position", "bucket", "region", "n_games", "n_clean",
          "n_puuid", "n_puuid_ge5", "n_timeline")


def render_console(patch, rows, exclusions):
    for champion in CHAMPIONS:
        subset = [r for r in rows if r["champion"] == champion]
        print(f"\n=== {champion} — patch {patch} ===")
        cells = [[str(r[f]) for f in FIELDS] for r in subset]
        widths = [max(len(HEADERS[i]), max(len(c[i]) for c in cells))
                  for i in range(len(HEADERS))]
        print(" | ".join(h.ljust(widths[i]) for i, h in enumerate(HEADERS)))
        print("-+-".join("-" * w for w in widths))
        for cell in cells:
            print(" | ".join(cell[i].ljust(widths[i])
                             for i in range(len(HEADERS))))

    print(f"\nExclusions — team_position inconnue :"
          f" {exclusions['excluded_unknown_position']} ligne(s) ;"
          f" puuid NULL sur game propre :"
          f" {exclusions['excluded_null_puuid']} ligne(s).")
    if exclusions["excluded_unknown_scope"]:
        print(f"ATTENTION : {exclusions['excluded_unknown_scope']} ligne(s) avec"
              " une région ou un bucket hors des constantes du §3, ignorées.")

    # Les deux lectures décisives (§8)
    ge5 = [r for r in rows if r["n_puuid_ge5"] > 0]
    print("\nLecture 1 — plafond architectural (n_puuid_ge5) :")
    if not ge5:
        print("    aucune cellule n'a un seul joueur à 5 games propres.")
    else:
        low = min(ge5, key=lambda r: r["n_puuid_ge5"])
        print(f"    plus petit non nul = {low['n_puuid_ge5']} joueur(s) —"
              f" {low['champion']} / {low['team_position']} / {low['bucket']}"
              f" / {low['region']} ({len(ge5)} cellule(s) non nulles).")

    with_clean = [r for r in rows if r["n_clean"] > 0 and r["region"] != ALL_REGIONS]
    print("Lecture 2 — couverture timeline par cellule :")
    if not with_clean:
        print("    aucune game propre : rien à mesurer.")
    else:
        rates = sorted(r["n_timeline"] / r["n_clean"] for r in with_clean)
        total_clean = sum(r["n_clean"] for r in with_clean)
        total_tl = sum(r["n_timeline"] for r in with_clean)
        median = rates[len(rates) // 2]
        print(f"    {len(with_clean)} cellule(s) régionales peuplées —"
              f" taux min {rates[0]:.2%}, médian {median:.2%},"
              f" max {rates[-1]:.2%} ; global"
              f" {total_tl}/{total_clean} = {total_tl / total_clean:.2%}.")


def write_json(out_dir, db_path, patch, rows, exclusions):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "db_path": str(db_path),
        "patch": patch,
        "filters": FILTERS,
        "champions": dict(CHAMPIONS),
        "excluded_unknown_position": exclusions["excluded_unknown_position"],
        "excluded_null_puuid": exclusions["excluded_null_puuid"],
        "rows": rows,
    }
    path = out_dir / f"coverage_{patch}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return path


# --- CLI --------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Mesure la couverture du dataset pour le module Coach"
                    " (lecture seule, collecteur en marche).")
    parser.add_argument("--patch", help="un patch précis, ex. 16.16")
    parser.add_argument("--all-patches", action="store_true",
                        help="tous les patchs présents, un JSON par patch")
    parser.add_argument("--db", default="data/matches.db")
    parser.add_argument("--out", default="out/coverage")
    return parser.parse_args(argv)


def _run(argv):
    args = parse_args(argv)
    if args.patch and args.all_patches:
        # code 1 : le code 2 est réservé à la détection de SCAN (§8)
        raise Fatal("--patch et --all-patches sont mutuellement exclusifs.")

    conn = open_db(args.db)
    try:
        check_champions(conn)
        available = list_patches(conn)
        if not available:
            raise Fatal(f"aucun patch dans {args.db}")

        if args.all_patches:
            patches = available
        elif args.patch:
            if args.patch not in available:
                raise Fatal(f"patch absent de la base : {args.patch}"
                            f" (présents : {', '.join(available)})")
            patches = [args.patch]
        else:
            patches = [available[-1]]

        for patch in patches:
            rows, exclusions = collect(conn, patch)
            render_console(patch, rows, exclusions)
            path = write_json(args.out, args.db, patch, rows, exclusions)
            print(f"\nJSON écrit : {path}")
    finally:
        conn.close()
    return EXIT_OK


def main(argv=None):
    try:
        return _run(argv)
    except CoverageExit as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
