#!/usr/bin/env python3
"""Tests du schéma étendu du collecteur (Lot 13) : dégâts, vision, fin de partie.

Base fixture dans `tmp_path`, jamais la base de production : ces tests
construisent des `Database`, donc appliquent les migrations.

Les tests de parsing s'appuient sur `tests/fixtures/match_v5_full.json`, une
réponse Match-V5 réelle (§7 de la spec) : les noms de champs Riot ne sont pas
crus sur parole. Tant que la fixture n'est pas capturée, ils sont ignorés
explicitement plutôt que joués sur un payload rédigé à la main.
"""

import copy
import json
import os
import sqlite3
import sys
import time

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("RIOT_API_KEY", "x")

from lolcollector.db import Database  # noqa: E402

FIXTURE = os.path.join(REPO, "tests", "fixtures", "match_v5_full.json")

# Colonne ajoutée -> champ de info.participants[]. Le préfixe "challenges."
# désigne un champ de l'objet challenges, absent de certains payloads réels.
PARTICIPANT_FIELDS = {
    "damage_to_champions": "totalDamageDealtToChampions",
    "damage_taken": "totalDamageTaken",
    "vision_score": "visionScore",
    "control_wards_bought": "visionWardsBoughtInGame",
    "time_spent_dead": "totalTimeSpentDead",
    "lane_cs_at10": "challenges.laneMinionsFirst10Minutes",
    "jungle_cs_at10": "challenges.jungleCsBefore10Minutes",
    "turret_plates_taken": "challenges.turretPlatesTaken",
    "heals_on_teammates": "totalHealsOnTeammates",
    "shields_on_teammates": "totalDamageShieldedOnTeammates",
}
PARTICIPANT_COLUMNS = list(PARTICIPANT_FIELDS)

# Colonne de `matches` -> champ pris sur info.participants[0] (identique aux 10).
MATCH_FIELD = ("early_surrender", "gameEndedInEarlySurrender")

BORNE = "lot13_migrated_at"

# Schéma d'avant le Lot 13, tel qu'il est en production : c'est sur celui-ci
# que la migration doit s'appliquer. Les autres tables sont créées par SCHEMA.
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id            TEXT PRIMARY KEY,
    region              TEXT NOT NULL,
    platform            TEXT NOT NULL,
    game_version        TEXT,
    patch               TEXT,
    game_duration       INTEGER,
    game_creation       INTEGER,
    tier_bucket_source  TEXT,
    inserted_at         INTEGER
);

CREATE TABLE IF NOT EXISTS participants (
    match_id            TEXT NOT NULL,
    puuid               TEXT,
    champion_id         INTEGER,
    champion_name       TEXT,
    team_id             INTEGER,
    team_position       TEXT,
    win                 INTEGER,
    kills               INTEGER,
    deaths              INTEGER,
    assists             INTEGER,
    item0 INTEGER, item1 INTEGER, item2 INTEGER, item3 INTEGER,
    item4 INTEGER, item5 INTEGER, item6 INTEGER,
    perk_primary_style  INTEGER,
    perk_sub_style      INTEGER,
    perk_keystone       INTEGER,
    gold_earned         INTEGER,
    total_cs            INTEGER,
    patch               TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def field(part, path):
    """Valeur brute d'un champ de participant, 'challenges.x' compris."""
    if path.startswith("challenges."):
        return (part.get("challenges") or {}).get(path.split(".", 1)[1])
    return part.get(path)


def expected(part, path):
    """Valeur attendue en base : les champs `challenges` sont convertis en
    entier à l'insertion (§4, arrondi et non troncature), les champs directs
    sont stockés bruts. Un champ absent reste NULL, jamais 0."""
    value = field(part, path)
    if path.startswith("challenges.") and value is not None:
        return int(round(value))
    return value


def make_old_db(path):
    """Une base à l'ancien schéma, avec un match déjà collecté."""
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO matches (match_id, region, platform, game_version, patch,"
        " game_duration, game_creation, tier_bucket_source, inserted_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("OLD_1", "europe", "euw1", "16.15.1.1", "16.15", 1800, 0,
         "SILVER_GOLD", int(time.time())),
    )
    conn.execute(
        "INSERT INTO participants (match_id, puuid, champion_id, patch)"
        " VALUES (?,?,?,?)", ("OLD_1", "pu_old", 1, "16.15"),
    )
    conn.commit()
    conn.close()


def stored_participants(db, match_id):
    """Les nouvelles colonnes des participants d'un match, dans l'ordre d'insertion."""
    sql = (f"SELECT {', '.join(PARTICIPANT_COLUMNS)} FROM participants"
           " WHERE match_id = ? ORDER BY rowid")
    return [dict(zip(PARTICIPANT_COLUMNS, row))
            for row in db.conn.execute(sql, (match_id,))]


@pytest.fixture
def payload():
    if not os.path.exists(FIXTURE):
        pytest.skip("fixture tests/fixtures/match_v5_full.json non capturée "
                    "(§7 : une requête Match-V5 réelle, pas un payload rédigé)")
    with open(FIXTURE) as fh:
        return json.load(fh)


# --- 1) migration d'une base existante --------------------------------------

def test_migration_ajoute_les_colonnes_et_pose_la_borne(tmp_path):
    path = str(tmp_path / "ancienne.db")
    make_old_db(path)
    db = Database(path)
    try:
        assert set(PARTICIPANT_COLUMNS) <= columns(db.conn, "participants")
        assert MATCH_FIELD[0] in columns(db.conn, "matches")

        # les lignes déjà collectées restent NULL : non collecté, pas mesuré nul
        row = db.conn.execute(
            f"SELECT {', '.join(PARTICIPANT_COLUMNS)} FROM participants"
            " WHERE match_id = 'OLD_1'").fetchone()
        assert row is not None
        assert all(value is None for value in row), row
        assert db.conn.execute(
            "SELECT early_surrender FROM matches WHERE match_id = 'OLD_1'"
        ).fetchone()[0] is None

        borne = db.get_meta(BORNE)
        assert borne is not None, "la borne temporelle des NULL n'est pas posée"
        assert abs(int(borne) - int(time.time())) < 300, borne
    finally:
        db.close()


# --- 2) idempotence ---------------------------------------------------------

def test_migration_idempotente(tmp_path):
    path = str(tmp_path / "ancienne.db")
    make_old_db(path)
    db = Database(path)
    borne = db.get_meta(BORNE)
    db.close()

    db = Database(path)          # ne doit pas lever
    try:
        assert db.get_meta(BORNE) == borne
        assert set(PARTICIPANT_COLUMNS) <= columns(db.conn, "participants")
        # la borne n'est jamais réécrite, même à valeur reconnaissable : sans
        # ça, une comparaison de deux constructions dans la même seconde
        # passerait sans rien prouver
        db.set_meta(BORNE, "1")
    finally:
        db.close()

    db = Database(path)
    try:
        assert db.get_meta(BORNE) == "1"
    finally:
        db.close()


# --- 3) base neuve ----------------------------------------------------------

def test_base_neuve_porte_les_colonnes(tmp_path):
    db = Database(str(tmp_path / "neuve.db"))
    try:
        assert set(PARTICIPANT_COLUMNS) <= columns(db.conn, "participants")
        assert MATCH_FIELD[0] in columns(db.conn, "matches")
    finally:
        db.close()


# --- 4) parsing d'un payload réel -------------------------------------------

def test_store_match_recopie_les_champs(tmp_path, payload):
    db = Database(str(tmp_path / "parse.db"))
    try:
        assert db.store_match(payload, "europe", "euw1", "SILVER_GOLD") is True
        match_id = payload["metadata"]["matchId"]
        rows = stored_participants(db, match_id)
        parts = payload["info"]["participants"]
        assert len(rows) == len(parts)
        for index, (row, part) in enumerate(zip(rows, parts)):
            for column, path in PARTICIPANT_FIELDS.items():
                assert row[column] == expected(part, path), \
                    f"participant {index}, {column} <- {path}"

        stored = db.conn.execute(
            "SELECT early_surrender FROM matches WHERE match_id = ?",
            (match_id,)).fetchone()[0]
        assert stored == (1 if parts[0].get(MATCH_FIELD[1]) else 0)
    finally:
        db.close()


def test_challenges_flottants_stockes_en_entier(tmp_path, payload):
    """Cas explicite de la fixture : jungleCsBefore10Minutes arrive en
    flottant (68.00000008940697). La colonne doit porter un entier, sinon le
    Lot 1 calcule ses percentiles sur une colonne à typage mixte."""
    parts = payload["info"]["participants"]
    flottants = [(index, field(part, "challenges.jungleCsBefore10Minutes"))
                 for index, part in enumerate(parts)
                 if isinstance(field(part, "challenges.jungleCsBefore10Minutes"), float)]
    assert flottants, ("la fixture ne porte plus de challenges flottant : "
                       "vérifier que le cas existe encore avant de relâcher la règle")

    db = Database(str(tmp_path / "flottant.db"))
    try:
        assert db.store_match(payload, "europe", "euw1", "SILVER_GOLD") is True
        rows = stored_participants(db, payload["metadata"]["matchId"])
        for index, brut in flottants:
            stocke = rows[index]["jungle_cs_at10"]
            assert stocke == int(round(brut)), (index, brut, stocke)
            assert isinstance(stocke, int), (index, brut, type(stocke))
            # arrondi, pas troncature : la valeur vraie est au-dessus de la
            # borne inférieure quand l'artefact est négatif (67.99999991)
            assert stocke == round(brut)

        # aucune colonne challenges à typage mixte, sur aucune ligne
        for column in ("lane_cs_at10", "jungle_cs_at10", "turret_plates_taken"):
            types = {row[0] for row in db.conn.execute(
                f"SELECT DISTINCT typeof({column}) FROM participants")}
            assert types <= {"integer", "null"}, (column, types)
    finally:
        db.close()


# --- 5) NULL = non collecté, 0 = mesuré nul ---------------------------------

def test_champs_absents_donnent_null(tmp_path, payload):
    data = copy.deepcopy(payload)
    data["metadata"]["matchId"] = "TEST_NULL_1"
    for part in data["info"]["participants"]:
        part.pop("challenges", None)      # cas réel sur certains matchs
        part.pop("visionScore", None)
        part.pop(MATCH_FIELD[1], None)

    db = Database(str(tmp_path / "null.db"))
    try:
        assert db.store_match(data, "europe", "euw1", "SILVER_GOLD") is True
        for row in stored_participants(db, "TEST_NULL_1"):
            for column, path in PARTICIPANT_FIELDS.items():
                if path.startswith("challenges.") or column == "vision_score":
                    assert row[column] is None, f"{column} devrait être NULL"
        assert db.conn.execute(
            "SELECT early_surrender FROM matches WHERE match_id = 'TEST_NULL_1'"
        ).fetchone()[0] is None
    finally:
        db.close()


def test_zero_mesure_reste_zero(tmp_path, payload):
    data = copy.deepcopy(payload)
    data["metadata"]["matchId"] = "TEST_ZERO_1"
    for part in data["info"]["participants"]:
        part["challenges"] = dict(part.get("challenges") or {})
        for column, path in PARTICIPANT_FIELDS.items():
            if path.startswith("challenges."):
                part["challenges"][path.split(".", 1)[1]] = 0
            else:
                part[path] = 0
        part[MATCH_FIELD[1]] = False

    db = Database(str(tmp_path / "zero.db"))
    try:
        assert db.store_match(data, "europe", "euw1", "SILVER_GOLD") is True
        for row in stored_participants(db, "TEST_ZERO_1"):
            for column in PARTICIPANT_COLUMNS:
                assert row[column] == 0, f"{column} : 0 mesuré, pas NULL"
        assert db.conn.execute(
            "SELECT early_surrender FROM matches WHERE match_id = 'TEST_ZERO_1'"
        ).fetchone()[0] == 0
    finally:
        db.close()


def test_early_surrender_vrai_stocke_1(tmp_path, payload):
    data = copy.deepcopy(payload)
    data["metadata"]["matchId"] = "TEST_ES_1"
    for part in data["info"]["participants"]:
        part[MATCH_FIELD[1]] = True

    db = Database(str(tmp_path / "es.db"))
    try:
        assert db.store_match(data, "europe", "euw1", "SILVER_GOLD") is True
        assert db.conn.execute(
            "SELECT early_surrender FROM matches WHERE match_id = 'TEST_ES_1'"
        ).fetchone()[0] == 1
    finally:
        db.close()


# --- 6) garde-fou : la fixture porte bien les champs du §3 ------------------

def test_fixture_porte_les_champs_attendus(payload):
    """Échoue si Riot renomme un champ ou si la spec contient une faute de
    frappe. Les champs directs sont exigés sur les 10 participants ; ceux de
    `challenges` sur au moins un, l'objet étant partiellement peuplé selon le
    rôle joué (jungleCsBefore10Minutes, turretPlatesTaken)."""
    info = payload["info"]
    parts = info["participants"]
    assert info["queueId"] == 420, "fixture hors ranked solo/duo : store_match la refuse"
    assert len(parts) == 10, len(parts)

    for column, path in PARTICIPANT_FIELDS.items():
        if path.startswith("challenges."):
            assert any(field(part, path) is not None for part in parts), \
                f"{path} absent de tous les participants ({column})"
        else:
            for index, part in enumerate(parts):
                assert path in part, f"{path} absent du participant {index} ({column})"

    assert any("challenges" in part for part in parts), "aucun objet challenges"
    assert MATCH_FIELD[1] in parts[0], MATCH_FIELD[1]
