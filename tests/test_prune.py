#!/usr/bin/env python3
"""Tests offline de la purge (collector.py prune)."""

import os
import shutil
import sqlite3
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
W = tempfile.mkdtemp(prefix="lolcollector-prune-")
os.chdir(W)
os.environ.update({"RIOT_API_KEY": "x", "DB_PATH": "data/matches.db", "LOG_DIR": "logs"})

from lolcollector.db import Database  # noqa: E402
from lolcollector.prune import exported_patches, run_prune  # noqa: E402
from lolcollector.timeline import store_timeline  # noqa: E402


def timeline(match_id, minutes=20):
    frames = []
    for m in range(minutes + 1):
        pf = {str(p): {"totalGold": 100 * m, "currentGold": 10, "xp": 50 * m,
                       "level": 5, "minionsKilled": 5 * m, "jungleMinionsKilled": 0,
                       "position": {"x": 1, "y": 2}} for p in range(1, 11)}
        frames.append({"timestamp": m * 60000, "participantFrames": pf,
                       "events": [{"type": "CHAMPION_KILL", "timestamp": m * 60000,
                                   "killerId": 1, "victimId": 2,
                                   "position": {"x": 1, "y": 2}}]})
    return {"info": {"frames": frames}}


db = Database("data/matches.db")
now = int(time.time())
cur = db.conn.cursor()
PATCHES = ["16.16", "16.15", "16.14", "16.13"]
for patch in PATCHES:
    for i in range(20):
        mid = f"{patch}_M{i}"
        cur.execute("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?,?)",
                    (mid, "europe", "euw1", patch + ".1.1", patch, 1800, 0,
                     "SILVER_GOLD", now))
        cur.execute("INSERT INTO participants (match_id, champion_id, patch)"
                    " VALUES (?,?,?)", (mid, 1, patch))
        cur.execute("INSERT INTO bans VALUES (?,?,?,?)", (mid, 100, 1, 1))
db.conn.commit()
for i in range(5):
    store_timeline(db, f"16.13_M{i}", timeline(f"16.13_M{i}"))
db.close()

# exports : 16.14 et 16.13 exportés, 16.15/16.16 non
for slug in ("16-14", "16-13"):
    d = os.path.join("site", "data", "etudes", "tierlist", slug)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "meta.json"), "w").write('{"patch": "%s"}' % slug.replace("-", "."))
assert exported_patches("site/data/etudes") == {"16.14", "16.13"}
print("OK  détection des exports :", sorted(exported_patches("site/data/etudes")))

conn = lambda: sqlite3.connect("data/matches.db")

# --- 1) keep=3 : seul 16.13 sort de la fenêtre, et il est exporté -> purgé
rc = run_prune("data/matches.db", keep_patches=3, exports_dir="site/data/etudes",
               assume_yes=True)
assert rc == 0
remaining = {p for (p,) in conn().execute("SELECT DISTINCT patch FROM matches")}
assert remaining == {"16.16", "16.15", "16.14"}, remaining
# ses timelines et lignes liées partent avec lui
for table in ("participants", "bans", "timeline_events", "timeline_frames",
              "timeline_state"):
    left = conn().execute(
        f"SELECT COUNT(*) FROM {table} WHERE match_id LIKE '16.13%'").fetchone()[0]
    assert left == 0, f"{table} garde {left} lignes orphelines de 16.13"
print("OK  keep=3 : 16.13 (exporté) purgé, aucune ligne orpheline "
      "(participants, bans, timelines)")

# --- 2) keep=2 : 16.14 sort de la fenêtre et est exporté -> purgé
rc = run_prune("data/matches.db", keep_patches=2, exports_dir="site/data/etudes",
               assume_yes=True)
assert rc == 0
remaining = {p for (p,) in conn().execute("SELECT DISTINCT patch FROM matches")}
assert remaining == {"16.16", "16.15"}, remaining
left = conn().execute(
    "SELECT COUNT(*) FROM participants WHERE match_id LIKE '16.14%'").fetchone()[0]
assert left == 0
print("OK  keep=2 : 16.14 (exporté) purgé")

# --- 3) patch non exporté : refus explicite, rien supprimé
d = os.path.join("site", "data", "etudes", "tierlist", "16-15")
assert not os.path.exists(d)
rc = run_prune("data/matches.db", keep_patches=1, exports_dir="site/data/etudes",
               assume_yes=True)
assert rc == 0
remaining = {p for (p,) in conn().execute("SELECT DISTINCT patch FROM matches")}
assert remaining == {"16.16", "16.15"}, f"16.15 purgé sans export ! {remaining}"
print("OK  garde-fou : 16.15 non exporté -> refus de purger")

# --- 4) une fois exporté, il devient purgeable
os.makedirs(d, exist_ok=True)
open(os.path.join(d, "meta.json"), "w").write('{"patch": "16.15"}')
rc = run_prune("data/matches.db", keep_patches=1, exports_dir="site/data/etudes",
               assume_yes=True)
remaining = {p for (p,) in conn().execute("SELECT DISTINCT patch FROM matches")}
assert remaining == {"16.16"}, remaining
print("OK  16.15 exporté -> purgeable, base réduite au seul patch courant")

# --- 5) rien à faire
rc = run_prune("data/matches.db", keep_patches=5, exports_dir="site/data/etudes",
               assume_yes=True)
assert rc == 0
print("OK  keep supérieur au nombre de patchs : no-op")

shutil.rmtree(W, ignore_errors=True)
print("TESTS PRUNE OK")
