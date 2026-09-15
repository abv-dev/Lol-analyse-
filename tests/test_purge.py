#!/usr/bin/env python3
"""Tests de la purge au changement de patch (collector.py purge-old-patches)
et du plancher de collecte qui empêche la base purgée de se re-remplir.

Bases fixtures dans `tmp_path`, jamais data/matches.db.
"""

import os
import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("RIOT_API_KEY", "x")

from lolcollector import compact  # noqa: E402
from lolcollector.compact import MATCH_TABLES, STATE_TABLES, run_purge  # noqa: E402
from lolcollector.db import (  # noqa: E402
    PURGE_MIN_PATCH,
    Database,
    collect_floor_patch,
    collect_since,
    collect_since_key,
    is_before_collect_floor,
)
from lolcollector.items import store_legendary_items  # noqa: E402
from lolcollector.timeline import store_timeline  # noqa: E402

DAY_MS = 86_400_000
NOW_MS = int(time.time() * 1000)
LEGENDARY = {3031}

# (match_id, région, patch, gameCreation ms, durée s, timeline ?) — ordre
# d'insertion volontairement entrelacé entre patchs, pour que les rowid des
# lignes gardées ne soient pas contigus. « 16.9 » est purgé bien que
# lexicographiquement supérieur à « 16.18 ».
MATCHES = [
    ("EUW1_1", "europe", "16.9", NOW_MS - 60 * DAY_MS, 1500, True),
    ("EUW1_2", "europe", "16.17", NOW_MS - 9 * DAY_MS, 1800, True),
    ("EUW1_3", "europe", "16.18", NOW_MS - 5 * DAY_MS, 1700, True),
    ("KR_1", "asia", "16.17", NOW_MS - 8 * DAY_MS, 2000, False),
    ("EUW1_4", "europe", "16.17", NOW_MS - 7 * DAY_MS, 1600, False),
    ("EUW1_5", "europe", "16.18", NOW_MS - 2 * DAY_MS, 1900, False),
]
KEPT = {m[0] for m in MATCHES if m[2] == "16.18"}


def match_payload(match_id, patch, creation, duration):
    return {
        "metadata": {"matchId": match_id},
        "info": {
            "queueId": 420, "gameVersion": f"{patch}.1.1",
            "gameCreation": creation, "gameDuration": duration,
            "participants": [
                {"participantId": i, "championId": 10 + i, "championName": f"C{i}",
                 "puuid": f"{match_id}-p{i}", "teamId": 100 if i <= 5 else 200,
                 "teamPosition": "TOP", "win": i <= 5, "kills": i}
                for i in range(1, 11)],
            "teams": [{"teamId": t, "bans": [{"championId": 50 + t, "pickTurn": 1}],
                       "objectives": {"champion": {"first": t == 100, "kills": 3}}}
                      for t in (100, 200)],
        },
    }


def timeline_payload(minutes=3):
    frames = []
    for m in range(minutes + 1):
        frames.append({
            "timestamp": m * 60000,
            "participantFrames": {str(p): {"totalGold": 100 * m, "currentGold": 10,
                                           "xp": 50 * m, "level": 1,
                                           "minionsKilled": m, "jungleMinionsKilled": 0,
                                           "position": {"x": 1, "y": 2}}
                                  for p in range(1, 11)},
            "events": [
                {"type": "CHAMPION_KILL", "timestamp": m * 60000, "killerId": 1,
                 "victimId": 6, "assistingParticipantIds": [2, 3],
                 "position": {"x": 1, "y": 2}},
                {"type": "ITEM_PURCHASED", "timestamp": m * 60000,
                 "participantId": 1, "itemId": 3031},
            ],
        })
    return {"info": {"frames": frames}}


@pytest.fixture
def base(tmp_path):
    path = str(tmp_path / "data" / "matches.db")
    db = Database(path)
    try:
        for match_id, region, patch, creation, duration, with_tl in MATCHES:
            assert db.store_match(match_payload(match_id, patch, creation, duration),
                                  region, "euw1", "SILVER_GOLD")
            if with_tl:
                store_timeline(db, match_id, timeline_payload(), LEGENDARY)
        db.set_meta("ddragon_current", "16.17.1")
        db.save_cursor("europe", "SILVER_GOLD", 1, 2, 3)
        store_legendary_items(db, "16.17", LEGENDARY, "16.17.1")
    finally:
        db.close()
    return path


@pytest.fixture
def cfg(base, tmp_path):
    return SimpleNamespace(db_path=base, pid_file=str(tmp_path / "collector.pid"))


def rows(path, table, where=""):
    conn = sqlite3.connect(path)
    try:
        return sorted(conn.execute(f"SELECT rowid, * FROM {table} {where}").fetchall(),
                      key=repr)
    finally:
        conn.close()


def fingerprint(path):
    st = os.stat(path)
    return (st.st_ino, st.st_size, st.st_mtime_ns)


def leftovers(path):
    directory = os.path.dirname(path)
    return sorted(f for f in os.listdir(directory)
                  if f.startswith(os.path.basename(path)) and f != os.path.basename(path))


def test_ne_garde_que_le_patch_courant(base, cfg):
    kept_in = "WHERE match_id IN ({})".format(",".join(f"'{m}'" for m in KEPT))
    before = {t: rows(base, t, kept_in) for t in ("matches", *MATCH_TABLES)}
    state_before = {t: rows(base, t) for t in ("sampling_state", "legendary_items")}
    assert all(before[t] for t in ("matches", "participants", "bans",
                                   "team_objectives", "timeline_events",
                                   "timeline_frames", "timeline_state", "item_events"))

    assert run_purge(cfg, "16.18", assume_yes=True, export=False) == 0

    # seules les lignes des matchs 16.18 restent, identiques, rowid compris
    # (le rang d'insertion porte participant_id)
    for table in ("matches", *MATCH_TABLES):
        assert rows(base, table) == before[table], table
    for table, content in state_before.items():
        assert rows(base, table) == content, table

    conn = sqlite3.connect(base)
    try:
        assert {p for (p,) in conn.execute("SELECT DISTINCT patch FROM matches")} == {"16.18"}
        assert conn.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert conn.execute("PRAGMA journal_mode").fetchone() == ("wal",)
        meta = dict(conn.execute("SELECT key, value FROM meta"))
    finally:
        conn.close()
    assert meta["ddragon_current"] == "16.17.1"
    assert meta[PURGE_MIN_PATCH] == "16.18"
    # europe : fin de la dernière partie purgée (EUW1_4) + 1 s
    assert meta[collect_since_key("europe")] == str(
        (NOW_MS - 7 * DAY_MS + 1600 * 1000) // 1000 + 1)
    assert meta[collect_since_key("asia")] == str(
        (NOW_MS - 8 * DAY_MS + 2000 * 1000) // 1000 + 1)
    assert leftovers(base) == []

    # le collecteur la rouvre et y écrit normalement
    db = Database(base)
    try:
        assert db.store_match(match_payload("EUW1_9", "16.18", NOW_MS - DAY_MS, 1500),
                              "europe", "euw1", "SILVER_GOLD")
    finally:
        db.close()


def test_rien_a_purger_ne_touche_pas_la_base(base, cfg):
    assert run_purge(cfg, "16.18", assume_yes=True, export=False) == 0
    avant = fingerprint(base)
    assert run_purge(cfg, "16.18", assume_yes=True, export=True) == 0
    assert fingerprint(base) == avant


def test_dry_run_ne_modifie_rien(base, cfg):
    avant = fingerprint(base)
    assert run_purge(cfg, "16.18", dry_run=True) == 0
    assert fingerprint(base) == avant
    assert leftovers(base) == []


def test_table_inconnue_refus(base, cfg):
    conn = sqlite3.connect(base)
    conn.execute("CREATE TABLE a_moi (x)")
    conn.commit()
    conn.close()
    avant = fingerprint(base)
    assert run_purge(cfg, "16.18", assume_yes=True, export=False) == 1
    assert fingerprint(base) == avant


def test_espace_insuffisant_refus(base, cfg, monkeypatch):
    monkeypatch.setattr(compact, "free_bytes", lambda path: 0)
    avant = fingerprint(base)
    assert run_purge(cfg, "16.18", assume_yes=True, export=False) == 1
    assert fingerprint(base) == avant
    assert leftovers(base) == []


@pytest.mark.parametrize("erreur", [RuntimeError("boum"), SystemExit("Export refusé")])
def test_export_du_patch_sortant_non_bloquant(base, cfg, monkeypatch, erreur):
    appels = []

    def export_en_echec(db_path, patch, out_dir, min_games):
        appels.append((patch, out_dir))
        raise erreur

    monkeypatch.setattr(compact, "export_tierlist", export_en_echec)
    assert run_purge(cfg, "16.18", assume_yes=True, export=True) == 0
    # le patch SORTANT (le plus récent des purgés, tri numérique), vers sa
    # destination par défaut
    assert appels == [("16.17", None)]
    assert rows(base, "matches", "WHERE patch <> '16.18'") == []


def test_collecteur_redemarre_meme_si_la_purge_echoue(base, cfg, monkeypatch):
    scripts = []
    monkeypatch.setattr(compact, "collector_running", lambda cfg: True)
    monkeypatch.setattr(compact, "_script", lambda name: scripts.append(name) or 0)

    def rebuild_en_echec(*args):
        raise RuntimeError("disque")

    monkeypatch.setattr(compact, "rebuild", rebuild_en_echec)
    avant = fingerprint(base)
    assert run_purge(cfg, "16.18", assume_yes=True, export=False) == 1
    assert scripts == ["stop.sh", "start.sh"]
    assert fingerprint(base) == avant


def test_patch_courant_repli_sur_la_base(base, monkeypatch):
    def injoignable(*args, **kwargs):
        raise OSError("réseau coupé")

    monkeypatch.setattr(compact.urllib.request, "urlopen", injoignable)
    assert compact.detect_current_patch(base) == "16.17"


# ---- plancher de collecte ----

def test_plancher_patch_le_plus_recent_numeriquement(tmp_path):
    db = Database(str(tmp_path / "p.db"))
    try:
        assert collect_floor_patch(db) is None
        db.set_meta("ddragon_current", "16.9.1")
        db.set_meta(PURGE_MIN_PATCH, "16.18")
        assert collect_floor_patch(db) == "16.18"
        db.set_meta("ddragon_current", "16.19.1")
        assert collect_floor_patch(db) == "16.19"
    finally:
        db.close()


def test_match_anterieur_ecarte_et_borne_remontee(tmp_path):
    db = Database(str(tmp_path / "p.db"))
    try:
        vieux = match_payload("EUW1_A", "16.9", NOW_MS - 3 * DAY_MS, 1800)
        assert not is_before_collect_floor(db, "europe", vieux)  # aucun plancher
        assert collect_since(db, "europe") == 0

        db.set_meta("ddragon_current", "16.18.1")
        assert is_before_collect_floor(db, "europe", vieux)
        fin = (NOW_MS - 3 * DAY_MS + 1800 * 1000) // 1000
        assert collect_since(db, "europe") == fin + 1
        assert collect_since(db, "asia") == 0  # borne par région

        # un rejet plus ancien ne fait jamais redescendre la borne
        plus_vieux = match_payload("EUW1_B", "16.17", NOW_MS - 6 * DAY_MS, 1800)
        assert is_before_collect_floor(db, "europe", plus_vieux)
        assert collect_since(db, "europe") == fin + 1

        # un match du patch courant passe, borne inchangée
        courant = match_payload("EUW1_C", "16.18", NOW_MS - DAY_MS, 1800)
        assert not is_before_collect_floor(db, "europe", courant)
        assert collect_since(db, "europe") == fin + 1

        # une fin de partie dans le futur ne bloque pas la collecte
        futur = match_payload("EUW1_D", "16.17", NOW_MS + 5 * DAY_MS, 1800)
        assert is_before_collect_floor(db, "europe", futur)
        assert collect_since(db, "europe") <= int(time.time())
    finally:
        db.close()
