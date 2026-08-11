"""Tests offline des timelines + mesure réelle du volume par match."""
import os, random, shutil, sqlite3, sys, time

import tempfile
# chemin du dépôt résolu AVANT tout chdir
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
W = tempfile.mkdtemp(prefix="lolcollector-timeline-")
os.chdir(W)
os.environ.update({"RIOT_API_KEY": "x", "DB_PATH": "data/matches.db", "LOG_DIR": "logs"})

from lolcollector.db import Database
from lolcollector.timeline import is_sampled, parse_timeline, store_timeline, mark_timeline

# --- 1) échantillonnage déterministe
ids = [f"EUW1_{i}" for i in range(20000)]
sel = [m for m in ids if is_sampled(m, 0.33)]
rate = len(sel) / len(ids)
assert 0.31 < rate < 0.35, rate
assert [m for m in ids if is_sampled(m, 0.33)] == sel, "non déterministe !"
assert all(is_sampled(m, 1.0) for m in ids[:50])
assert not any(is_sampled(m, 0.0) for m in ids[:50])
# monotone : ce qui est pris à 0.2 l'est aussi à 0.5
assert set(m for m in ids if is_sampled(m, 0.2)) <= set(m for m in ids if is_sampled(m, 0.5))
print(f"OK  échantillonnage : {rate*100:.2f}% pour rate=0.33, déterministe et monotone")

# --- 2) parsing : types conservés, grubs vs herald
def fake_timeline(match_id, minutes=28):
    frames = []
    for m in range(minutes + 1):
        pf = {str(p): {"totalGold": 500*m+p, "currentGold": 100+p, "xp": 300*m,
                       "level": min(18, 1+m//2), "minionsKilled": 6*m,
                       "jungleMinionsKilled": m, "position": {"x": 1000+p, "y": 2000+m}}
              for p in range(1, 11)}
        events = [{"type": "ITEM_PURCHASED", "timestamp": m*60000, "itemId": 3006},
                  {"type": "LEVEL_UP", "timestamp": m*60000},
                  {"type": "WARD_PLACED", "timestamp": m*60000}]
        if m == 8:
            events.append({"type": "ELITE_MONSTER_KILL", "timestamp": 8*60000,
                           "killerId": 3, "monsterType": "HORDE", "teamId": 100,
                           "position": {"x": 5000, "y": 5000}})
        if m == 14:
            events.append({"type": "ELITE_MONSTER_KILL", "timestamp": 14*60000,
                           "killerId": 3, "monsterType": "RIFTHERALD", "teamId": 100,
                           "position": {"x": 5100, "y": 5100}})
        if m == 16:
            events.append({"type": "ELITE_MONSTER_KILL", "timestamp": 16*60000,
                           "killerId": 4, "monsterType": "DRAGON",
                           "monsterSubType": "FIRE_DRAGON", "teamId": 200,
                           "position": {"x": 9000, "y": 4000}})
        if m == 10:
            events.append({"type": "TURRET_PLATE_DESTROYED", "timestamp": 10*60000,
                           "teamId": 200, "laneType": "TOP_LANE",
                           "position": {"x": 4000, "y": 12000}})
        if m == 20:
            events.append({"type": "BUILDING_KILL", "timestamp": 20*60000,
                           "teamId": 200, "buildingType": "TOWER_BUILDING",
                           "laneType": "MID_LANE", "killerId": 1,
                           "position": {"x": 7000, "y": 7000}})
        for k in range(3):
            events.append({"type": "CHAMPION_KILL", "timestamp": m*60000+k*1000,
                           "killerId": 1+k, "victimId": 6+k,
                           "position": {"x": 3000+k, "y": 3000+k}})
        frames.append({"timestamp": m*60000, "participantFrames": pf, "events": events})
    return {"info": {"frames": frames}}

ev, fr = parse_timeline("EUW1_TEST", fake_timeline("EUW1_TEST"))
types = {e[2] for e in ev}
assert types == {"CHAMPION_KILL", "ELITE_MONSTER_KILL", "BUILDING_KILL",
                 "TURRET_PLATE_DESTROYED"}, types
assert not any(e[2] in ("ITEM_PURCHASED", "LEVEL_UP", "WARD_PLACED") for e in ev)
monsters = {e[6] for e in ev if e[2] == "ELITE_MONSTER_KILL"}
assert monsters == {"HORDE", "RIFTHERALD", "DRAGON"}, monsters
assert len(fr) == 29 * 10, len(fr)
cs_last = [f[7] for f in fr if f[1] == 28][0]
assert cs_last == 6*28 + 28, cs_last   # minions + jungle
print(f"OK  parsing : {len(ev)} events (types filtrés), {len(fr)} frames, "
      f"HORDE et RIFTHERALD distincts")

# --- 3) stockage + idempotence
db = Database("data/matches.db")
n_ev, n_fr = store_timeline(db, "EUW1_TEST", fake_timeline("EUW1_TEST"))
assert (n_ev, n_fr) == (len(ev), len(fr))
store_timeline(db, "EUW1_TEST", fake_timeline("EUW1_TEST"))  # re-stockage
assert db.conn.execute("SELECT COUNT(*) FROM timeline_events").fetchone()[0] == n_ev
assert db.conn.execute("SELECT COUNT(*) FROM timeline_frames").fetchone()[0] == n_fr
assert db.conn.execute("SELECT status FROM timeline_state WHERE match_id='EUW1_TEST'"
                       ).fetchone()[0] == "ok"
mark_timeline(db, "EUW1_SKIP", "skipped")
assert db.conn.execute("SELECT status FROM timeline_state WHERE match_id='EUW1_SKIP'"
                       ).fetchone()[0] == "skipped"
print("OK  stockage idempotent (re-stockage sans doublon) + états")

# --- 4) horde_kills dans team_objectives
def fake_match(mid):
    parts = [{"puuid": f"p{i}", "championId": 100+i, "championName": f"C{i}",
              "teamId": 100 if i < 5 else 200, "teamPosition": "TOP", "win": i < 5,
              "kills": 1, "deaths": 1, "assists": 1, "goldEarned": 1,
              "totalMinionsKilled": 1, "neutralMinionsKilled": 1,
              "perks": {"styles": []}} for i in range(10)]
    teams = [{"teamId": t, "bans": [], "objectives": {
        "champion": {"first": t == 100, "kills": 10},
        "tower": {"first": True, "kills": 5},
        "dragon": {"first": False, "kills": 2},
        "baron": {"first": True, "kills": 1},
        "riftHerald": {"first": True, "kills": 1},
        "horde": {"first": True, "kills": 5},
    }} for t in (100, 200)]
    return {"metadata": {"matchId": mid},
            "info": {"queueId": 420, "gameVersion": "16.15.1.1", "gameDuration": 1700,
                     "gameCreation": 0, "participants": parts, "teams": teams}}
db.store_match(fake_match("EUW1_OBJ"), "europe", "euw1", "SILVER_GOLD")
h, g = db.conn.execute("SELECT herald_kills, horde_kills FROM team_objectives"
                       " WHERE match_id='EUW1_OBJ' AND team_id=100").fetchone()
assert (h, g) == (1, 5), (h, g)
print(f"OK  objectifs : herald_kills={h} (RIFTHERALD) et horde_kills={g} (voidgrubs) séparés")
db.close()

# --- 5) MESURE du volume réel par match (avec index)
shutil.rmtree("vol", ignore_errors=True); os.makedirs("vol")
db2 = Database("vol/measure.db")
random.seed(7)
N = 300
for i in range(N):
    mins = random.randint(18, 40)
    store_timeline(db2, f"EUW1_V{i}", fake_timeline(f"EUW1_V{i}", mins))
db2.conn.execute("VACUUM")
db2.close()
size = os.path.getsize("vol/measure.db")
per_match = size / N
ev_n = sqlite3.connect("vol/measure.db").execute("SELECT COUNT(*) FROM timeline_events").fetchone()[0]
fr_n = sqlite3.connect("vol/measure.db").execute("SELECT COUNT(*) FROM timeline_frames").fetchone()[0]
print(f"\nMESURE VOLUME sur {N} timelines (durées 18-40 min, index inclus, après VACUUM) :")
print(f"  base {size/1024/1024:.1f} Mo -> {per_match/1024:.1f} Ko par match")
print(f"  {ev_n/N:.0f} events et {fr_n/N:.0f} frames par match en moyenne")
for share, label in ((0.33, "33 %"), (1.0, "100 %")):
    for n_matches, lbl in ((786509, "patch 16.15 (786 509 matchs)"),
                           (2190000, "base entière (2,19 M matchs)")):
        print(f"  {label} de {lbl} : {per_match*n_matches*share/1024**3:.1f} Go")
print("\nTESTS TIMELINE OK")
