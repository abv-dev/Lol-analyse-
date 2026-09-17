#!/usr/bin/env python3
"""Tests hors ligne du générateur d'études (scripts/generate_study.py).

Base SQLite fixture (> 20 000 matchs par région), rédacteur `claude` simulé,
dépôt temporaire : aucun appel réseau, aucune écriture dans le vrai dépôt.

    python3 tests/test_generate_study.py
"""

import hashlib
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
W = tempfile.mkdtemp(prefix="elolab-gen-")
os.environ.update({"RIOT_API_KEY": "x", "LOG_DIR": os.path.join(W, "logs")})

from lolcollector.db import Database  # noqa: E402

NAMES = ["Alpha", "Bravo", "Cobalt", "Delta", "Echo", "Fauve", "Garance", "Hélix",
         "Iris", "Jade", "Kilo", "Lima", "Mistral", "Nova", "Onyx", "Pampa", "Quartz",
         "Rubis", "Sierra", "Tango", "Ulysse", "Vesta", "Whisky", "Xénon", "Yucca",
         "Zénith", "Ambre", "Basalte", "Cyan", "Dune", "Ébène", "Fjord", "Givre",
         "Houle", "Ivoire", "Jaspe", "Kaolin", "Lagune", "Mica", "Nacre"]
ROLES = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
REGIONS = {"europe": "euw1", "asia": "kr", "americas": "na1"}
BUCKETS = ["IRON_BRONZE", "SILVER_GOLD", "PLAT_EMERALD", "DIAMOND_PLUS"]


def strength(champ, region, bucket):
    s = (champ - 20) * 0.004
    if champ == 3:                       # écart de rank marqué
        s += 0.06 if bucket == "DIAMOND_PLUS" else (-0.06 if bucket == "IRON_BRONZE" else 0)
    if champ == 7:                       # écart régional marqué
        s += 0.05 if region == "asia" else -0.02
    return s


def seed(path, per_cell, patch="16.18"):
    rng = random.Random(42)
    db = Database(path)
    now = int(time.time()) - 5 * 86400
    m, p, b = [], [], []
    mid = 0
    weights = [3 if c < 10 else 1 for c in range(len(NAMES))]  # populaires
    for region, platform in REGIONS.items():
        for bucket in BUCKETS:
            for _ in range(per_cell):
                mid += 1
                match_id = f"{platform.upper()}_{mid}"
                m.append((match_id, region, platform, f"{patch}.700.1", patch, 1800,
                          now * 1000, bucket, now + mid))
                champs = []
                while len(champs) < 10:
                    c = rng.choices(range(len(NAMES)), weights)[0]
                    if c not in champs:
                        champs.append(c)
                s100 = sum(strength(c, region, bucket) for c in champs[:5])
                s200 = sum(strength(c, region, bucket) for c in champs[5:])
                win100 = rng.random() < 0.5 + (s100 - s200) / 2
                for slot, c in enumerate(champs):
                    team = 100 if slot < 5 else 200
                    win = int(win100 if team == 100 else not win100)
                    p.append((match_id, f"pu{mid}_{slot}", c + 1, NAMES[c], team,
                              ROLES[slot % 5], win, patch))
                for k, c in enumerate(rng.sample(range(12), 2)):
                    b.append((match_id, 100 if k == 0 else 200, c + 1, k + 1))
    cur = db.conn.cursor()
    cur.executemany("INSERT INTO matches (match_id, region, platform, game_version, patch,"
                    " game_duration, game_creation, tier_bucket_source, inserted_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)", m)
    cur.executemany("INSERT INTO participants (match_id, puuid, champion_id, champion_name,"
                    " team_id, team_position, win, patch) VALUES (?,?,?,?,?,?,?,?)", p)
    cur.executemany("INSERT INTO bans VALUES (?,?,?,?)", b)
    db.conn.commit()
    db.close()


def make_repo(name):
    root = os.path.join(W, name)
    for rel in ("docs/editorial.md", "site/content/etudes/tierlist/16-15/index.mdx",
                "site/content/etudes/tierlist/16-15/meta.json",
                "scripts/verify_generated.py", "scripts/verify_study.py",
                "studies/topics.json"):
        os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
        shutil.copy(os.path.join(REPO, rel), os.path.join(root, rel))
    shutil.copytree(os.path.join(REPO, "site/data/etudes/tierlist/16-15"),
                    os.path.join(root, "site/data/etudes/tierlist/16-15"))
    with open(os.path.join(root, "studies/state.json"), "w") as fh:
        json.dump({"published": []}, fh)
    return root


FAKE_CLAUDE = r'''
import json, sys, os
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "fail":
    print("appel interdit", file=sys.stderr); sys.exit(9)
precis = prompt.split("=== PRÉCIS (seule source de vérité) ===\n", 1)[1]
precis = json.loads(precis.split("\n=== TENTATIVE", 1)[0])
sections = []
for s in precis["sections"]:
    d = s["donnees"]
    texte = "Aucun champion ne remplit ce critère sur ce patch."
    if d.get("lignes"):
        row = d["lignes"][0]
        fid = next(iter(row["faits"]))
        texte = f"{row['champion']} affiche {{{{{fid}}}}} dans cette section."
    elif d.get("faits_globaux"):
        fid = next(iter(d["faits_globaux"]))
        texte = f"Le décompte donne {{{{{fid}}}}}."
    if mode == "digits":
        texte += " Soit 12 champions au total."
    sections.append({"key": s["key"], "titre": "Résultat mesuré de la section", "texte": texte})
out = {"description": "Mesures du patch sur {{global.matches}} matchs classés.",
       "chapo": "**Le résultat principal tient dans les tableaux.** Les intervalles tranchent.",
       "sections": sections,
       "retenir": "Les écarts mesurés se lisent avec leurs intervalles de confiance."}
print(json.dumps({"is_error": False, "subtype": "success", "total_cost_usd": 0,
                  "structured_output": out}))
'''


def gen(repo, db, *args, mode="ok"):
    env = {**os.environ, "ELOLAB_REPO": repo, "ELOLAB_OFFLINE": "1", "FAKE_MODE": mode,
           "ELOLAB_CLAUDE_CMD": json.dumps([sys.executable, FAKE]),
           "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "generate_study.py"),
                           *args, "--db", db, "--no-git", "--skip-build"],
                          capture_output=True, text=True, encoding="utf-8", env=env)
    return proc.returncode, proc.stdout + proc.stderr


def verify(repo, rel):
    proc = subprocess.run([sys.executable, os.path.join(repo, "scripts", "verify_generated.py"),
                           os.path.join(repo, rel)], capture_output=True, text=True,
                          encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return proc.returncode, proc.stdout


def digest(path):
    h = hashlib.sha256()
    for suffix in ("", "-wal"):
        if os.path.exists(path + suffix):
            with open(path + suffix, "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()


FAKE = os.path.join(W, "fake_claude.py")
with open(FAKE, "w", encoding="utf-8") as fh:
    fh.write(FAKE_CLAUDE)

DB = os.path.join(W, "matches.db")
t0 = time.time()
seed(DB, per_cell=5100)          # 20 400 matchs par région
print(f"fixture : {time.time() - t0:.1f}s")
db_before = digest(DB)

# --- 1) étude hebdomadaire nominale -----------------------------------------
repo = make_repo("repo1")
code, out = gen(repo, DB, "weekly")
assert code == 0, out
state = json.load(open(os.path.join(repo, "studies/state.json")))
assert [e["topic"] for e in state["published"]] == ["comparaison-regionale"], state
rel = "site/content/etudes/regions/16-18"
mdx = open(os.path.join(repo, rel, "index.mdx"), encoding="utf-8").read()
assert "<TierTable />" in mdx and "<IntervalChart" in mdx and "{{" not in mdx
code, vout = verify(repo, rel)
assert code == 0, vout
print(f"OK  sujet 1 généré (comparaison-regionale), vérification : {vout.strip()}")

# --- 2) rotation : la semaine suivante prend le sujet suivant ---------------
code, out = gen(repo, DB, "weekly")
assert code == 0, out
state = json.load(open(os.path.join(repo, "studies/state.json")))
assert [e["topic"] for e in state["published"]][-1] == "champions-pieges", state
assert verify(repo, "site/content/etudes/champions-pieges/16-18")[0] == 0
print("OK  rotation : le run suivant publie champions-pieges")

# --- 3) falsification : un chiffre du mauvais champion est détecté ----------
path = os.path.join(repo, rel, "index.mdx")
facts = json.load(open(os.path.join(repo, "site/data/etudes/regions/16-18/study.json"),
                       encoding="utf-8"))["facts"]
wr = [(k, v) for k, v in facts.items() if k.endswith(".wr") and v["champion_id"]]
(k1, f1), (k2, f2) = wr[0], next(x for x in wr if x[1]["champion_id"] != wr[0][1]["champion_id"]
                                  and x[1]["display"] != wr[0][1]["display"])
assert f1["display"] in mdx
open(path, "w", encoding="utf-8").write(mdx.replace(f1["display"], f2["display"], 1))
code, vout = verify(repo, rel)
assert code == 1 and "ne correspond à aucune donnée" in vout, vout
open(path, "w", encoding="utf-8").write(mdx.replace("<StudyMeta />", "<StudyMeta />\n\nUn écart de 7,42 points.", 1))
assert verify(repo, rel)[0] == 1
open(path, "w", encoding="utf-8").write(mdx)
print("OK  vérificateur : winrate d'un autre champion et chiffre inventé rejetés")

# --- 4) le rédacteur écrit un chiffre en dur : rejeté, rien de publié -------
repo2 = make_repo("repo2")
code, out = gen(repo2, DB, "weekly", mode="digits")
assert code == 3, out
assert "chiffre écrit en dur « 12 »" in out, out
assert not os.path.exists(os.path.join(repo2, "site/content/etudes/regions"))
assert json.load(open(os.path.join(repo2, "studies/state.json")))["published"] == []
print("OK  chiffre écrit par le modèle : 3 tentatives rejetées, rien écrit (code 3)")

# --- 5) tier list du patch : sans LLM, une seule fois par patch -------------
repo3 = make_repo("repo3")
code, out = gen(repo3, DB, "tierlist", mode="fail")
assert code == 0, out
assert "tentative" not in out, out   # aucun appel au rédacteur (FAKE_MODE=fail l'aurait fait échouer)
rel_t = "site/content/etudes/tierlist/16-18"
assert verify(repo3, rel_t)[0] == 0
proc = subprocess.run([sys.executable, os.path.join(repo3, "scripts/verify_study.py"),
                       os.path.join(repo3, rel_t)], capture_output=True, text=True,
                      encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
assert proc.returncode == 0, proc.stdout
code, out = gen(repo3, DB, "tierlist", mode="fail")
assert code == 0 and "déjà publiée" in out, out
print("OK  tier list : générée sans appel au modèle, verify_study et verify_generated verts, "
      "pas de doublon")

# --- 6) gate de couverture : une région sous 20 000 matchs ------------------
DB_THIN = os.path.join(W, "thin.db")
shutil.copy(DB, DB_THIN)
conn = sqlite3.connect(DB_THIN)
conn.execute("DELETE FROM matches WHERE match_id IN (SELECT match_id FROM matches"
             " WHERE region = 'europe' LIMIT 1000)")
conn.commit()
conn.close()
repo4 = make_repo("repo4")
for mode_ in ("weekly", "tierlist"):
    code, out = gen(repo4, DB_THIN, mode_, mode="fail")
    assert code == 2, out
    assert "europe n'a que 19400 matchs" in out, out
assert not os.path.exists(os.path.join(repo4, "site/content/etudes/regions"))
assert not os.path.exists(os.path.join(repo4, "site/content/etudes/tierlist/16-18"))
print("OK  gate : europe à 19 400 matchs -> arrêt code 2, rien écrit, modèle jamais appelé")

# --- 7) la base n'a jamais été modifiée ------------------------------------
assert digest(DB) == db_before, "matches.db a été modifiée"
from lolcollector.studygen import open_readonly  # noqa: E402
conn = open_readonly(DB)
try:
    conn.execute("CREATE TABLE t (x)")
    raise AssertionError("écriture possible sur la connexion en lecture seule")
except sqlite3.OperationalError:
    pass
conn.close()
print("OK  matches.db intacte ; la connexion du générateur refuse toute écriture")

shutil.rmtree(W, ignore_errors=True)
print("TESTS GÉNÉRATEUR OK")
