#!/usr/bin/env python3
"""Tests offline des garde-fous d'export et de la dimension rôle.

Rejoue l'incident : un export du patch 16.16 (quelques centaines de matchs,
aucune cellule exploitable) lancé vers le dossier publié de 16.15.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
W = tempfile.mkdtemp(prefix="elolab-export-")
os.chdir(W)
os.environ.update({"RIOT_API_KEY": "x", "DB_PATH": "data/matches.db",
                   "LOG_DIR": "logs"})

from lolcollector.db import Database  # noqa: E402
from lolcollector.export import (  # noqa: E402
    ROLES, default_out_dir, export_tierlist,
)

ROLE_LIST = list(ROLES)
REGIONS = {"europe": "euw1", "asia": "kr"}
BUCKETS = ["IRON_BRONZE", "DIAMOND_PLUS"]


def seed(db, patch, version, per_cell, champs=6):
    """Chaque champion joue tous les rôles, pour que la dimension rôle soit
    réellement peuplée ; un participant sur dix a un team_position vide."""
    m, p, b = [], [], []
    mid = 0
    now = int(time.time())
    for region, platform in REGIONS.items():
        for bucket in BUCKETS:
            for _ in range(per_cell):
                mid += 1
                match_id = f"{platform.upper()}_{patch}_{mid}"
                m.append((match_id, region, platform, version, patch, 1800,
                          now * 1000, now, bucket, now))
                for slot in range(10):
                    champ = 1 + (slot + mid) % champs
                    role = ROLE_LIST[slot % 5]
                    stored = "" if (mid + slot) % 10 == 0 else role
                    p.append((match_id, f"pu{mid}_{slot}", champ, f"Champ{champ}",
                              100 if slot < 5 else 200, stored,
                              1 if slot < 5 else 0,
                              0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, patch))
                b.append((match_id, 100, 1 + mid % champs, 1))
    cur = db.conn.cursor()
    cur.executemany("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?,?)",
                    [(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[8], r[9]) for r in m])
    cur.executemany(
        "INSERT INTO participants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", p)
    cur.executemany("INSERT INTO bans VALUES (?,?,?,?)", b)
    db.conn.commit()


os.makedirs("data", exist_ok=True)
db = Database("data/matches.db")
seed(db, "16.15", "16.15.700.1", per_cell=400)   # ~4000 games/cellule : exploitable
seed(db, "16.16", "16.16.700.1", per_cell=12)    # ~120 games : sous le seuil
db.close()

PUBLISHED = os.path.join("site", "data", "etudes", "tierlist", "16-15")


def export(patch, out=None, **kw):
    return export_tierlist("data/matches.db", patch, out, **kw)


# --- 1) export nominal 16.15 vers la destination déduite --------------------

meta = export("16.15")
assert meta["out_dir"] == PUBLISHED, meta["out_dir"]
assert meta["patch"] == "16.15"
assert meta["usable_cells"] > 0
published = json.load(open(os.path.join(PUBLISHED, "tierlist.json")))
print(f"OK  export nominal : {len(published)} cellules -> {PUBLISHED} (chemin déduit)")

# --- 2) dimension rôle ------------------------------------------------------

roles = json.load(open(os.path.join(PUBLISHED, "tierlist-roles.json")))
assert {r["role"] for r in roles} == set(ROLE_LIST), {r["role"] for r in roles}

# les agrégats tous rôles sont EXACTEMENT la somme des rôles + les positions
# vides : c'est la même passe SQL, ils ne peuvent pas diverger
by_all = {(r["champion_id"], r["region"], r["bucket"]): r for r in published}
summed = {}
for r in roles:
    key = (r["champion_id"], r["region"], r["bucket"])
    acc = summed.setdefault(key, [0, 0])
    acc[0] += r["games"]
    acc[1] += r["wins"]
empty = 0
for key, cell in by_all.items():
    games, wins = summed.get(key, (0, 0))
    assert cell["games"] >= games, (key, cell["games"], games)
    empty += cell["games"] - games
assert empty > 0, "le jeu de test doit contenir des team_position vides"
print(f"OK  rôles : 5 postes, {len(roles)} cellules ; "
      f"{empty} participations sans poste comptées dans « tous rôles » seulement")

# le seuil s'applique bien à la cellule PAR RÔLE, plus petite
assert any(r["games"] < meta["min_cell_games"] for r in roles)
assert "bans" not in roles[0] and "winrate" not in roles[0], roles[0]
print("OK  seuil appliqué par cellule de rôle ; ni bans ni champs dérivés")

# --- 3) INCIDENT : 16.16 vers le dossier publié de 16.15 --------------------

before = open(os.path.join(PUBLISHED, "tierlist.json")).read()
try:
    export("16.16", PUBLISHED)
    raise AssertionError("l'export aurait dû être refusé")
except SystemExit as exc:
    assert "16.16" in str(exc) and "16.15" in str(exc), exc
assert open(os.path.join(PUBLISHED, "tierlist.json")).read() == before
print("OK  incident rejoué : 16.16 vers 16-15/ refusé, fichiers publiés intacts")

# --- 4) --force ne contourne PAS le contrôle de patch ----------------------

try:
    export("16.16", PUBLISHED, force=True)
    raise AssertionError("--force n'a pas à contourner le contrôle de patch")
except SystemExit:
    pass
assert open(os.path.join(PUBLISHED, "tierlist.json")).read() == before
print("OK  --force ne contourne pas le contrôle de patch")

# --- 5) dossier sans slug mais contenant l'export d'un autre patch ---------

NEUTRAL = os.path.join(W, "un-dossier-quelconque")
os.makedirs(NEUTRAL, exist_ok=True)
shutil.copy(os.path.join(PUBLISHED, "meta.json"), NEUTRAL)  # meta de 16.15
try:
    export("16.16", NEUTRAL)
    raise AssertionError("le meta.json existant aurait dû bloquer")
except SystemExit as exc:
    assert "16.15" in str(exc), exc
print("OK  meta.json d'un autre patch dans la destination : refusé aussi")

# --- 6) bon patch, mauvaise base : export bien plus maigre que le publié ---
# Cas réellement rencontré : un export de test lancé sur une base synthétique
# a écrasé les données publiées du même patch. Ni le contrôle de patch ni
# celui des cellules ne le voient.
big = json.load(open(os.path.join(PUBLISHED, "meta.json")))
big["total_matches"] = 786_509
with open(os.path.join(PUBLISHED, "meta.json"), "w") as fh:
    json.dump(big, fh)
try:
    export("16.15")          # la base de test n'a que 1 600 matchs
    raise AssertionError("un export dix fois plus maigre aurait dû être refusé")
except SystemExit as exc:
    assert "mauvaise base" in str(exc), exc
print("OK  bon patch mais base trop maigre : refusé (786 509 -> 1 600)")

meta_forced = export("16.15", force=True)
assert meta_forced["total_matches"] == 1600
print("OK  --force passe outre l'écart de volume")

# --- 7) export sans aucune cellule exploitable -----------------------------

try:
    export("16.16")           # destination déduite : .../16-16/, patch cohérent
    raise AssertionError("un export sans cellule exploitable aurait dû être refusé")
except SystemExit as exc:
    assert "aucune cellule" in str(exc), exc
assert not os.path.exists(default_out_dir("tierlist", "16.16")), \
    "le dossier ne doit même pas être créé"
print("OK  export sans cellule au seuil : refusé, dossier non créé")

meta16 = export("16.16", force=True)
assert meta16["usable_cells"] == 0
assert os.path.exists(os.path.join(default_out_dir("tierlist", "16.16"),
                                   "tierlist.json"))
print("OK  --force écrit quand même, dans le bon dossier")

# --- 8) reconstruction d'un index dont la définition a changé --------------

conn = sqlite3.connect("data/matches.db")
conn.execute("DROP INDEX idx_participants_export")
conn.execute("CREATE INDEX idx_participants_export ON participants"
             " (match_id, champion_id, win)")   # ancienne définition
conn.commit()
conn.close()
export("16.15")   # doit détecter l'écart et reconstruire, sans planter
conn = sqlite3.connect("data/matches.db")
sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = "
                   "'idx_participants_export'").fetchone()[0]
conn.close()
assert "team_position" in sql, sql
print("OK  index obsolète détecté et reconstruit avec team_position")

shutil.rmtree(W, ignore_errors=True)
print("TESTS EXPORT OK")
