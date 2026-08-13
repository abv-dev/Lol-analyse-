#!/usr/bin/env python3
"""Tests offline de la file éditoriale.

Le point à vérifier n'est pas l'affichage, c'est la règle de sélection : le
jour où un patch sort, la file doit rester alimentée par le structurel et le
comparatif au lieu de se vider.
"""

import os
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
W = tempfile.mkdtemp(prefix="elolab-queue-")
os.chdir(W)
os.environ.update({"RIOT_API_KEY": "x", "DB_PATH": "data/matches.db",
                   "LOG_DIR": "logs",
                   # seuil de volume abaissé : le jeu de test compte en
                   # milliers de matchs, pas en centaines de milliers
                   "QUEUE_MIN_PATCH_MATCHES": "5000"})

from lolcollector.db import Database  # noqa: E402
from lolcollector.editorial import (  # noqa: E402
    MAX_ATTEMPTS, DataState, blockers, instantiate, load_json, selection_order,
    stock_count, sync_templates,
)

TEMPLATES = load_json(os.path.join(REPO, "queue", "templates.json"))["templates"]


def seed(db, patch, version, matches, days_ago, roles=True, grubs=False, start=0):
    """`start` décale les identifiants : re-seeder un patch ajoute des matchs
    au lieu de violer la clé primaire."""
    created = int((time.time() - days_ago * 86400) * 1000)
    m, p, o = [], [], []
    for i in range(start, start + matches):
        mid = f"M_{patch}_{i}"
        region = ["europe", "asia", "americas"][i % 3]
        m.append((mid, region, "euw1", version, patch, 1800, created,
                  "SILVER_GOLD", int(time.time())))
        for slot in range(10):
            pos = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"][slot % 5]
            p.append((mid, f"pu{i}_{slot}", 1 + slot, "C", 100 if slot < 5 else 200,
                      pos if roles else "", slot < 5,
                      0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, patch))
        o.append((mid, 100, 1, 1, 1, 1, 1, 1, 1, 1, 2 if grubs else None))
    cur = db.conn.cursor()
    cur.executemany("INSERT OR IGNORE INTO matches VALUES (?,?,?,?,?,?,?,?,?)", m)
    cur.executemany(
        "INSERT INTO participants VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", p)
    cur.executemany(
        "INSERT INTO team_objectives VALUES (?,?,?,?,?,?,?,?,?,?,?)", o)
    db.conn.commit()


STRUCTUREL = {
    "id": "struct-exemple", "slug": "x/y", "famille": "objectifs",
    "titre": "Un structurel", "angle": "…", "regime": "structurel",
    "donnees_requises": ["matches"], "patch_cible": None,
    "statut": "en_attente", "tentatives": 0,
}
BLOQUE_PAR_TIMELINE = {
    **STRUCTUREL, "id": "struct-timeline",
    "debloque_par": "timeline_frames",
}
BLOQUE_PAR_GRUBS = {
    **STRUCTUREL, "id": "struct-grubs", "debloque_par": ["horde_kills"],
}
DONNEE_INCONNUE = {
    **STRUCTUREL, "id": "struct-typo", "donnees_requises": ["team_positon"],
}

# --- 1) gabarits : 11 gabarits -> 15 articles par patch --------------------

created = instantiate(TEMPLATES, "16.15")
assert len(TEMPLATES) == 11, len(TEMPLATES)
assert len(created) == 15, len(created)
roles = [a for a in created if a["gabarit"] == "meta-role"]
assert len(roles) == 5
assert {a["id"] for a in roles} == {
    f"meta-role-{r}-16-15" for r in ("top", "jungle", "mid", "bot", "support")}
assert roles[0]["titre"] == "Méta par poste — Top, patch 16.15", roles[0]["titre"]
assert roles[0]["slug"] == "meta-role/top-16-15", roles[0]["slug"]
assert all(a["patch_cible"] == "16.15" for a in created)
print(f"OK  {len(TEMPLATES)} gabarits -> {len(created)} articles pour 16.15, "
      f"dont 5 par poste")

# --- 2) sync idempotent -----------------------------------------------------

queue = {"articles": []}
assert len(sync_templates(queue, TEMPLATES, "16.15")) == 15
assert sync_templates(queue, TEMPLATES, "16.15") == []
assert len(queue["articles"]) == 15
queue["articles"][0]["statut"] = "publie"
sync_templates(queue, TEMPLATES, "16.15")
assert queue["articles"][0]["statut"] == "publie", "un re-sync a écrasé un statut"
assert len(sync_templates(queue, TEMPLATES, "16.16")) == 15
print("OK  sync idempotent : ni doublon ni statut écrasé, patch suivant ajouté")

# --- 3) patch mûr : les patch_courant sont rédigeables ----------------------

os.makedirs("data", exist_ok=True)
db = Database("data/matches.db")
seed(db, "16.14", "16.14.1", matches=6000, days_ago=20)
seed(db, "16.15", "16.15.1", matches=6000, days_ago=9)
db.close()

state = DataState("data/matches.db")
assert state.patch == "16.15", state.patch
assert state.previous_patch == "16.14"
assert state.patch_age_days > 8
assert state.patch_mature, (state.patch_age_days, state.total_matches)

articles = instantiate(TEMPLATES, "16.15") + [STRUCTUREL]
ordered = selection_order(articles, state, stock=20, stock_min=15)
assert any(a["regime"] == "patch_courant" for a in ordered)
assert ordered[0]["regime"] == "patch_courant", ordered[0]
print(f"OK  patch mûr ({state.patch_age_days:.0f} j, {state.total_matches} matchs) : "
      f"{len(ordered)} rédigeables, le patch courant passe devant")

# --- 4) patch tout juste sorti : la file ne se vide PAS --------------------

db = Database("data/matches.db")
seed(db, "16.16", "16.16.1", matches=300, days_ago=1)   # 1 jour, 300 matchs
db.close()

state = DataState("data/matches.db")
assert state.patch == "16.16" and not state.patch_mature

articles = instantiate(TEMPLATES, "16.16") + [STRUCTUREL]
ordered = selection_order(articles, state, stock=20, stock_min=15)
regimes = {a["regime"] for a in ordered}
assert "patch_courant" not in regimes, "un patch de 1 jour ne doit rien porter"
assert "structurel" in regimes, "le réservoir doit rester disponible"
reasons = blockers([a for a in articles if a["regime"] == "patch_courant"][0], state)
assert any("trop jeune" in r for r in reasons), reasons
assert any("volume insuffisant" in r for r in reasons), reasons
print(f"OK  patch de 1 jour : aucun patch_courant, mais {len(ordered)} rédigeables "
      f"({', '.join(sorted(regimes))}) — la file ne se vide pas")

# --- 5) stock bas : le structurel passe devant ------------------------------

db = Database("data/matches.db")
seed(db, "16.16", "16.16.1", matches=6000, days_ago=5, start=1000)  # devient mûr
db.close()
state = DataState("data/matches.db")
assert state.patch_mature

articles = instantiate(TEMPLATES, "16.16") + [STRUCTUREL]
plein = selection_order(articles, state, stock=20, stock_min=15)
bas = selection_order(articles, state, stock=3, stock_min=15)
assert plein[0]["regime"] == "patch_courant"
assert bas[0]["regime"] == "structurel", bas[0]
print("OK  stock bas : le structurel passe devant ; stock plein : le patch courant")

# --- 6) debloque_par : invisible tant que la donnée manque -----------------

assert blockers(BLOQUE_PAR_TIMELINE, state), "devrait attendre les timelines"
assert any("en attente de" in r and "frames" in r
           for r in blockers(BLOQUE_PAR_TIMELINE, state)), \
    blockers(BLOQUE_PAR_TIMELINE, state)
assert blockers(BLOQUE_PAR_GRUBS, state)
assert not blockers(STRUCTUREL, state), blockers(STRUCTUREL, state)
print("OK  debloque_par : structurelle invisible tant que la donnée manque")

# la même structurelle devient rédigeable d'elle-même quand la donnée arrive
db = Database("data/matches.db")
seed(db, "16.16", "16.16.1", matches=100, days_ago=5, grubs=True, start=20000)
db.conn.execute("UPDATE team_objectives SET horde_kills = 2")
db.conn.commit()
db.close()
state = DataState("data/matches.db")
assert not blockers(BLOQUE_PAR_GRUBS, state), blockers(BLOQUE_PAR_GRUBS, state)
print("OK  … et redevient rédigeable automatiquement quand elle arrive")

# --- 7) une donnée inconnue ne rend pas l'article rédigeable ---------------

reasons = blockers(DONNEE_INCONNUE, state)
assert any("inconnue" in r for r in reasons), reasons
print("OK  faute de frappe dans donnees_requises : bloque au lieu de passer")

# --- 8) statuts et tentatives ----------------------------------------------

assert blockers({**STRUCTUREL, "statut": "verifie"}, state) == ["déjà au statut « verifie »"]
assert blockers({**STRUCTUREL, "tentatives": MAX_ATTEMPTS}, state)
assert stock_count([{"statut": "verifie"}, {"statut": "redige"},
                    {"statut": "verifie"}]) == 2
print("OK  statuts terminaux, plafond de tentatives, comptage du stock")

import shutil  # noqa: E402
shutil.rmtree(W, ignore_errors=True)
print("TESTS FILE ÉDITORIALE OK")
