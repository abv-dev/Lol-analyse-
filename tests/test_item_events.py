#!/usr/bin/env python3
"""Tests du Lot 14 : participant_id, item_events légendaires, assisting_ids.

Base fixture dans `tmp_path`, jamais la base de production : ces tests
construisent des `Database`, donc appliquent les migrations, et le backfill de
`participant_id` écrit. Rien ici ne touche `data/matches.db`.

Deux fixtures réelles, capturées comme celle du Lot 13 (§7 de la spec 13) :

- `tests/fixtures/timeline_v5_full.json` : réponse Match-V5 /timelines
  complète (EUW1_7948908611, patch 16.16), puuids anonymisés — aucun nom de
  champ ni structure touchés. Elle porte les trois cas qui comptent : des
  achats légendaires et non légendaires, une annulation d'achat légendaire,
  des kills assistés et des kills solo.
- `tests/fixtures/ddragon_item_16_16.json` : item.json de Data Dragon 16.16.1,
  entrées recopiées telles quelles ; seules des entrées ont été RETIRÉES (les
  99 conservées couvrent tous les objets achetés dans la timeline ci-dessus
  plus un représentant réel de chaque cas de rejet du filtre).
"""

import copy
import json
import logging
import os
import sqlite3
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("RIOT_API_KEY", "x")

from lolcollector.db import (  # noqa: E402
    Database,
    ParticipantIdError,
    backfill_participant_ids,
    check_participant_ids,
)
from lolcollector.items import (  # noqa: E402
    LegendaryItems,
    is_legendary,
    legendary_for_patch,
    legendary_ids,
    load_legendary_items,
    store_legendary_items,
)
from lolcollector.timeline import (  # noqa: E402
    parse_item_events,
    parse_timeline,
    store_timeline,
)

FIXTURES = os.path.join(REPO, "tests", "fixtures")
MATCH_FIXTURE = os.path.join(FIXTURES, "match_v5_full.json")
TIMELINE_FIXTURE = os.path.join(FIXTURES, "timeline_v5_full.json")
ITEM_FIXTURE = os.path.join(FIXTURES, "ddragon_item_16_16.json")

# Index de `assisting_ids` dans les tuples rendus par parse_timeline : la
# colonne est ajoutée EN FIN de tuple, les positions existantes ne bougent pas.
ASSISTING = 12

# Schéma d'avant le Lot 14, tel qu'il est en production (Lot 13 appliqué) :
# c'est sur celui-ci que la migration doit s'appliquer.
OLD_SCHEMA = """
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
    patch               TEXT,
    damage_to_champions  INTEGER,
    damage_taken         INTEGER,
    vision_score         INTEGER,
    control_wards_bought INTEGER,
    time_spent_dead      INTEGER,
    lane_cs_at10         INTEGER,
    jungle_cs_at10       INTEGER,
    turret_plates_taken  INTEGER,
    heals_on_teammates   INTEGER,
    shields_on_teammates INTEGER
);

CREATE TABLE IF NOT EXISTS timeline_events (
    match_id        TEXT NOT NULL,
    timestamp_ms    INTEGER,
    type            TEXT,
    team_id         INTEGER,
    killer_id       INTEGER,
    victim_id       INTEGER,
    monster_type    TEXT,
    monster_subtype TEXT,
    lane_type       TEXT,
    building_type   TEXT,
    position_x      INTEGER,
    position_y      INTEGER
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def tables(conn):
    return {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def load(path, why):
    if not os.path.exists(path):
        pytest.skip(f"fixture {os.path.relpath(path, REPO)} non capturée ({why})")
    with open(path) as fh:
        return json.load(fh)


@pytest.fixture
def timeline():
    return load(TIMELINE_FIXTURE, "§5 : une timeline réelle, pas un payload rédigé")


@pytest.fixture
def match():
    return load(MATCH_FIXTURE, "fixture Match-V5 du Lot 13")


@pytest.fixture
def ddragon():
    return load(ITEM_FIXTURE, "§3.1 : item.json réel de Data Dragon")


@pytest.fixture
def legendary(ddragon):
    return legendary_ids(ddragon)


def insert_old_participants(conn, match_id, puuids):
    """Lignes participants à l'ancien schéma : insérées dans l'ordre du payload,
    sans participant_id (c'est l'état de la base de production)."""
    conn.executemany(
        "INSERT INTO participants (match_id, puuid, champion_id, patch)"
        " VALUES (?,?,?,?)",
        [(match_id, puuid, 100 + i, "16.15") for i, puuid in enumerate(puuids)],
    )


# --- 1) migration : colonnes, tables, idempotence ---------------------------

def test_migration_ajoute_colonnes_et_tables(tmp_path):
    path = str(tmp_path / "ancienne.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    insert_old_participants(conn, "OLD_1", [f"pu_{i}" for i in range(10)])
    conn.execute(
        "INSERT INTO timeline_events (match_id, timestamp_ms, type, killer_id,"
        " victim_id) VALUES (?,?,?,?,?)", ("OLD_1", 1000, "CHAMPION_KILL", 1, 6))
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        assert "participant_id" in columns(db.conn, "participants")
        assert "assisting_ids" in columns(db.conn, "timeline_events")
        assert {"item_events", "legendary_items"} <= tables(db.conn)

        # lignes antérieures : NULL, jamais réécrites — c'est ce qui permet aux
        # lots aval d'exclure les timelines pré-Lot 14
        assert all(row[0] is None for row in db.conn.execute(
            "SELECT participant_id FROM participants WHERE match_id = 'OLD_1'"))
        assert db.conn.execute(
            "SELECT assisting_ids FROM timeline_events WHERE match_id = 'OLD_1'"
        ).fetchone()[0] is None
    finally:
        db.close()


def test_migration_idempotente(tmp_path):
    path = str(tmp_path / "ancienne.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()

    db = Database(path)
    schema_1 = sorted(row[0] for row in db.conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
    db.close()

    db = Database(path)          # seconde application : ne doit pas lever
    try:
        schema_2 = sorted(row[0] for row in db.conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        assert schema_2 == schema_1
    finally:
        db.close()


def test_base_neuve_porte_le_schema_complet(tmp_path):
    db = Database(str(tmp_path / "neuve.db"))
    try:
        assert "participant_id" in columns(db.conn, "participants")
        assert "assisting_ids" in columns(db.conn, "timeline_events")
        assert {"item_events", "legendary_items"} <= tables(db.conn)
        assert columns(db.conn, "item_events") == {
            "match_id", "participant_id", "item_id", "timestamp_ms", "event"}
        assert columns(db.conn, "legendary_items") == {"patch", "item_id"}
    finally:
        db.close()


# --- 2) backfill de participant_id ------------------------------------------

def test_backfill_derive_le_participant_id_du_rang(tmp_path, match):
    """Le rang d'insertion vaut le participantId Riot (§2.2) : vérifié contre
    le payload Match-V5 réel, puuid par puuid, pas sur parole."""
    path = str(tmp_path / "backfill.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    parts = match["info"]["participants"]
    insert_old_participants(conn, "BF_1", [p["puuid"] for p in parts])
    # un second match, inséré après : le rang doit repartir de 1 par match_id
    insert_old_participants(conn, "BF_2", [f"autre_{i}" for i in range(10)])
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        assert backfill_participant_ids(db.conn) == 20
        attendu = {p["puuid"]: p["participantId"] for p in parts}
        stocke = dict(db.conn.execute(
            "SELECT puuid, participant_id FROM participants WHERE match_id = 'BF_1'"))
        assert stocke == attendu
        assert sorted(row[0] for row in db.conn.execute(
            "SELECT participant_id FROM participants WHERE match_id = 'BF_2'"
        )) == list(range(1, 11))

        # relancé, il n'a plus rien à faire : seules les lignes NULL sont remplies
        assert backfill_participant_ids(db.conn) == 0
        assert dict(db.conn.execute(
            "SELECT puuid, participant_id FROM participants"
            " WHERE match_id = 'BF_1'")) == attendu
    finally:
        db.close()


def test_backfill_garde_fou_sur_doublon(tmp_path):
    """Fixture à doublon : un match dont une ligne porte déjà un
    participant_id qui entrera en collision avec le rang. Le garde-fou doit
    déclencher et TOUT annuler — y compris le match sain."""
    path = str(tmp_path / "doublon.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    insert_old_participants(conn, "SAIN", [f"ok_{i}" for i in range(10)])
    insert_old_participants(conn, "DOUBLON", [f"ko_{i}" for i in range(10)])
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        # la 2e ligne du match porte déjà 1 : le backfill ne remplit que les
        # NULL, le rang donnera donc 1 à la première -> deux fois 1 sur le match
        db.conn.execute("UPDATE participants SET participant_id = 1"
                        " WHERE match_id = 'DOUBLON' AND puuid = 'ko_1'")
        db.conn.commit()
        with pytest.raises(ParticipantIdError) as exc:
            backfill_participant_ids(db.conn)
        assert "DOUBLON" in str(exc.value)

        # rollback : rien n'est écrit, pas même le match sain
        restants = [row[0] for row in db.conn.execute(
            "SELECT participant_id FROM participants WHERE match_id = 'SAIN'")]
        assert restants == [None] * 10
        assert sorted(row[0] for row in db.conn.execute(
            "SELECT participant_id FROM participants WHERE match_id = 'DOUBLON'"
        ) if row[0] is not None) == [1]
    finally:
        db.close()


def test_check_participant_ids_liste_les_matchs_incoherents(tmp_path):
    path = str(tmp_path / "check.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    insert_old_participants(conn, "SAIN", [f"ok_{i}" for i in range(10)])
    insert_old_participants(conn, "TROUE", [f"ko_{i}" for i in range(10)])
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        backfill_participant_ids(db.conn)
        assert check_participant_ids(db.conn) == (0, [])

        # 11 au lieu de 10 : hors de 1..10
        db.conn.execute("UPDATE participants SET participant_id = 11"
                        " WHERE match_id = 'TROUE' AND puuid = 'ko_0'")
        total, exemples = check_participant_ids(db.conn)
        assert total == 1 and exemples == ["TROUE"]

        # un match à moins de 10 lignes n'est pas contrôlé (§2.2)
        db.conn.execute(
            "INSERT INTO participants (match_id, puuid, participant_id)"
            " VALUES ('PARTIEL', 'x', 4)")
        assert check_participant_ids(db.conn)[0] == 1
    finally:
        db.close()


# --- 3) store_match renseigne participant_id --------------------------------

def test_store_match_renseigne_participant_id(tmp_path, match):
    db = Database(str(tmp_path / "store.db"))
    try:
        assert db.store_match(match, "europe", "euw1", "SILVER_GOLD") is True
        parts = match["info"]["participants"]
        stocke = dict(db.conn.execute(
            "SELECT puuid, participant_id FROM participants WHERE match_id = ?",
            (match["metadata"]["matchId"],)))
        assert stocke == {p["puuid"]: p["participantId"] for p in parts}
        # aucun NULL sur une insertion neuve (§2.1)
        assert db.conn.execute(
            "SELECT COUNT(*) FROM participants WHERE participant_id IS NULL"
        ).fetchone()[0] == 0
    finally:
        db.close()


def test_fixture_match_porte_participant_id(match):
    """Garde-fou : le champ existe bien dans le payload réel, sur les 10."""
    parts = match["info"]["participants"]
    assert [p["participantId"] for p in parts] == list(range(1, 11))


# --- 4) filtre légendaire ---------------------------------------------------

def test_filtre_legendaire_sur_item_json_reel(ddragon, legendary):
    data = ddragon["data"]
    assert ddragon["version"].startswith("16.16")

    # chaque cas de rejet est représenté par un objet réel du patch 16.16
    rejets = {
        "1054": "complet mais < 2000 or (Doran's Shield)",
        "2526": "composant : `into` non vide (Whispering Circlet, 2250)",
        "2530": "gold.purchasable faux (Diadem of Songs)",
        "123430": "absent de la carte 11 (Rite of Ruin, 3000)",
    }
    for item_id, pourquoi in rejets.items():
        assert item_id in data, f"cas de rejet absent de la fixture : {pourquoi}"
        assert not is_legendary(data[item_id]), pourquoi
        assert int(item_id) not in legendary, pourquoi

    # et un légendaire réel passe
    assert is_legendary(data["6665"]), data["6665"]["name"]
    assert 6665 in legendary

    # invariants du filtre sur l'ensemble de la fixture
    for item_id in legendary:
        item = data[str(item_id)]
        assert item["gold"]["purchasable"] is True
        assert not item.get("into")
        assert item["gold"]["total"] >= 2000
        assert item["maps"]["11"] is True
    assert len(legendary) >= 20, len(legendary)


def test_legendary_items_stockes_et_relus(tmp_path, ddragon, legendary):
    db = Database(str(tmp_path / "leg.db"))
    try:
        store_legendary_items(db, "16.16", legendary, ddragon["version"])
        assert load_legendary_items(db, "16.16") == legendary
        assert load_legendary_items(db, "16.15") == set()
        # traçabilité : la version Data Dragon utilisée est en meta (§3.1)
        assert db.get_meta("legendary_ddragon_16.16") == ddragon["version"]

        # re-stockage : pas de doublon (clé primaire (patch, item_id))
        store_legendary_items(db, "16.16", legendary, ddragon["version"])
        assert db.conn.execute(
            "SELECT COUNT(*) FROM legendary_items WHERE patch = '16.16'"
        ).fetchone()[0] == len(legendary)
    finally:
        db.close()


def test_repli_sur_le_patch_precedent(tmp_path, ddragon, legendary, caplog):
    db = Database(str(tmp_path / "repli.db"))
    try:
        store_legendary_items(db, "16.9", {1, 2}, "16.9.1")
        store_legendary_items(db, "16.15", legendary, "16.15.1")

        with caplog.at_level(logging.WARNING):
            ids, source = legendary_for_patch(db, "16.16",
                                              logging.getLogger("test_repli"))
        # repli sur 16.15 et pas 16.9 : tri numérique, pas lexicographique
        assert source == "16.15"
        assert ids == legendary
        assert "16.15" in caplog.text and "16.16" in caplog.text

        # patch connu : aucun repli, aucun journal
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            ids, source = legendary_for_patch(db, "16.15",
                                              logging.getLogger("test_repli"))
        assert (source, ids) == ("16.15", legendary)
        assert caplog.text == ""

        # aucune liste du tout : ensemble vide, la collecte n'est pas bloquée
        db.conn.execute("DELETE FROM legendary_items")
        db.conn.commit()
        assert legendary_for_patch(db, "16.16") == (set(), None)
    finally:
        db.close()


class FakeClient:
    """Client ddragon minimal : versions.json + item.json, ou panne réseau."""

    def __init__(self, versions, payload, panne=False):
        self.versions = versions
        self.payload = payload
        self.panne = panne
        self.appels = 0

    async def ddragon_versions(self):
        if self.panne:
            raise OSError("réseau indisponible")
        return self.versions

    async def ddragon_items(self, version):
        self.appels += 1
        if self.panne:
            raise OSError("réseau indisponible")
        return self.payload


def test_chargement_au_premier_match_dun_patch_inconnu(tmp_path, ddragon, legendary):
    import asyncio

    db = Database(str(tmp_path / "cache.db"))
    try:
        cache = LegendaryItems(db, logging.getLogger("test_cache"))
        client = FakeClient(["16.16.1", "16.15.1"], ddragon)

        assert asyncio.run(cache.ids_for(client, "16.16")) == legendary
        assert load_legendary_items(db, "16.16") == legendary   # persisté
        assert client.appels == 1

        # deuxième match du même patch : plus aucun appel réseau
        assert asyncio.run(cache.ids_for(client, "16.16")) == legendary
        assert client.appels == 1
    finally:
        db.close()


def test_panne_reseau_repli_et_retry_espace(tmp_path, ddragon, legendary, caplog):
    import asyncio

    db = Database(str(tmp_path / "panne.db"))
    try:
        store_legendary_items(db, "16.15", legendary, "16.15.1")
        cache = LegendaryItems(db, logging.getLogger("test_panne"),
                               retry_interval=3600)
        client = FakeClient(["16.16.1"], ddragon, panne=True)

        with caplog.at_level(logging.WARNING):
            ids = asyncio.run(cache.ids_for(client, "16.16"))
        assert ids == legendary, "le repli sur le patch précédent n'a pas joué"
        assert load_legendary_items(db, "16.16") == set()
        assert caplog.text

        # retenté au plus une fois par intervalle : pas de martèlement
        asyncio.run(cache.ids_for(client, "16.16"))
        assert client.appels <= 1

        # le réseau revient et l'intervalle est passé : la liste se charge
        client.panne = False
        cache._next_try = 0
        assert asyncio.run(cache.ids_for(client, "16.16")) == legendary
        assert load_legendary_items(db, "16.16") == legendary
    finally:
        db.close()


# --- 5) parsing des achats légendaires --------------------------------------

def achats_du_payload(timeline):
    """(achats, annulations) bruts de la timeline réelle."""
    achats, undo = [], []
    for frame in timeline["info"]["frames"]:
        for event in frame["events"]:
            if event["type"] == "ITEM_PURCHASED":
                achats.append(event)
            elif event["type"] == "ITEM_UNDO":
                undo.append(event)
    return achats, undo


def test_fixture_timeline_porte_les_cas_utiles(timeline, legendary):
    achats, undo = achats_du_payload(timeline)
    leg_achats = [e for e in achats if e["itemId"] in legendary]
    leg_undo = [e for e in undo if e.get("beforeId") in legendary]
    assert leg_achats, "aucun achat légendaire dans la fixture"
    assert len(leg_achats) < len(achats), "aucun achat NON légendaire à écarter"
    assert leg_undo, "aucune annulation d'achat légendaire dans la fixture"

    kills = [e for frame in timeline["info"]["frames"] for e in frame["events"]
             if e["type"] == "CHAMPION_KILL"]
    assert any(e.get("assistingParticipantIds") for e in kills), "aucun kill assisté"
    assert any(not e.get("assistingParticipantIds") for e in kills), "aucun solo kill"


def test_parse_item_events_ne_garde_que_le_legendaire(timeline, legendary):
    achats, undo = achats_du_payload(timeline)
    evenements = parse_item_events("EUW1_TL", timeline, legendary)

    attendu = [("EUW1_TL", e["participantId"], e["itemId"], e["timestamp"],
                "PURCHASED") for e in achats if e["itemId"] in legendary]
    attendu += [("EUW1_TL", e["participantId"], e["beforeId"], e["timestamp"],
                 "UNDO") for e in undo if e.get("beforeId") in legendary]
    assert sorted(evenements) == sorted(attendu)

    gardes = {(e[2], e[4]) for e in evenements}
    for event in achats:
        if event["itemId"] not in legendary:
            assert (event["itemId"], "PURCHASED") not in gardes, event["itemId"]
    for event in undo:
        if event.get("beforeId") not in legendary:
            assert (event["beforeId"], "UNDO") not in gardes, event["beforeId"]

    # sans liste, rien n'est extrait — la collecte de timelines continue
    assert parse_item_events("EUW1_TL", timeline, set()) == []
    assert parse_item_events("EUW1_TL", timeline, None) == []


def test_kept_event_types_inchange(timeline, legendary):
    """Le §3.2 interdit d'élargir timeline_events aux achats : le chiffrage du
    §6 repose dessus (365 Mo/jour contre 28)."""
    from lolcollector.timeline import KEPT_EVENT_TYPES
    assert KEPT_EVENT_TYPES == {"CHAMPION_KILL", "ELITE_MONSTER_KILL",
                                "BUILDING_KILL", "TURRET_PLATE_DESTROYED"}
    events, _ = parse_timeline("EUW1_TL", timeline)
    assert not any(e[2] in ("ITEM_PURCHASED", "ITEM_UNDO") for e in events)


def test_store_timeline_ecrit_les_item_events(tmp_path, timeline, legendary):
    db = Database(str(tmp_path / "items.db"))
    try:
        store_timeline(db, "EUW1_TL", timeline, legendary)
        attendu = parse_item_events("EUW1_TL", timeline, legendary)
        lignes = db.conn.execute(
            "SELECT match_id, participant_id, item_id, timestamp_ms, event"
            " FROM item_events WHERE match_id = 'EUW1_TL'").fetchall()
        assert sorted(lignes) == sorted(attendu)
        assert {row[4] for row in lignes} <= {"PURCHASED", "UNDO"}

        # même chemin que les events/frames : DELETE préalable, pas de doublon
        store_timeline(db, "EUW1_TL", timeline, legendary)
        assert db.conn.execute(
            "SELECT COUNT(*) FROM item_events WHERE match_id = 'EUW1_TL'"
        ).fetchone()[0] == len(attendu)

        # sans liste (patch inconnu, réseau tombé) : timeline stockée quand même
        store_timeline(db, "EUW1_AUTRE", timeline)
        assert db.conn.execute(
            "SELECT COUNT(*) FROM timeline_events WHERE match_id = 'EUW1_AUTRE'"
        ).fetchone()[0] > 0
        assert db.conn.execute(
            "SELECT COUNT(*) FROM item_events WHERE match_id = 'EUW1_AUTRE'"
        ).fetchone()[0] == 0
    finally:
        db.close()


# --- 6) assisting_ids -------------------------------------------------------

def test_assisting_ids_sur_timeline_reelle(timeline):
    events, _ = parse_timeline("EUW1_TL", timeline)
    bruts = [e for frame in timeline["info"]["frames"] for e in frame["events"]
             if e["type"] == "CHAMPION_KILL"]
    kills = [e for e in events if e[2] == "CHAMPION_KILL"]
    assert len(kills) == len(bruts)

    for ligne, brut in zip(kills, bruts):
        assistants = brut.get("assistingParticipantIds") or []
        # ids dans l'ordre du payload, séparés par des virgules
        assert ligne[ASSISTING] == ",".join(str(i) for i in assistants)

    assistes = [e for e in kills if e[ASSISTING]]
    solos = [e for e in kills if e[ASSISTING] == ""]
    assert assistes and solos
    assert len(assistes) + len(solos) == len(kills)
    # chaîne vide = kill mesuré sans assistant, jamais NULL (§4)
    assert not any(e[ASSISTING] is None for e in kills)
    exemple = next(e for e in assistes if "," in e[ASSISTING])
    assert all(part.isdigit() for part in exemple[ASSISTING].split(","))

    # les autres types d'événements restent NULL
    autres = [e for e in events if e[2] != "CHAMPION_KILL"]
    assert autres and all(e[ASSISTING] is None for e in autres)


def test_assisting_ids_en_base(tmp_path, timeline):
    db = Database(str(tmp_path / "assist.db"))
    try:
        store_timeline(db, "EUW1_TL", timeline)
        valeurs = [row[0] for row in db.conn.execute(
            "SELECT assisting_ids FROM timeline_events"
            " WHERE match_id = 'EUW1_TL' AND type = 'CHAMPION_KILL'")]
        assert valeurs and None not in valeurs
        assert "" in valeurs                       # solo kill mesuré
        assert any("," in v for v in valeurs)      # kill à plusieurs assistants
        assert all(row[0] is None for row in db.conn.execute(
            "SELECT assisting_ids FROM timeline_events"
            " WHERE match_id = 'EUW1_TL' AND type <> 'CHAMPION_KILL'"))
    finally:
        db.close()


def test_lignes_pre_migration_restent_null(tmp_path, timeline):
    """La distinction NULL / '' est ce qui permet aux lots aval d'exclure les
    timelines antérieures au Lot 14 sur cette métrique seulement."""
    path = str(tmp_path / "melange.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO timeline_events (match_id, timestamp_ms, type, killer_id,"
        " victim_id) VALUES (?,?,?,?,?)", ("VIEUX", 1000, "CHAMPION_KILL", 1, 6))
    conn.commit()
    conn.close()

    db = Database(path)
    try:
        store_timeline(db, "NEUF", timeline)
        assert db.conn.execute(
            "SELECT assisting_ids FROM timeline_events WHERE match_id = 'VIEUX'"
        ).fetchone()[0] is None
        anciens = db.conn.execute(
            "SELECT COUNT(*) FROM timeline_events"
            " WHERE type = 'CHAMPION_KILL' AND assisting_ids IS NULL").fetchone()[0]
        assert anciens == 1, "une timeline collectée après migration ne doit pas " \
                             "produire de CHAMPION_KILL à NULL"
    finally:
        db.close()


def test_prune_emporte_les_item_events(tmp_path, timeline, legendary):
    """`item_events` suit le même cycle de vie que les autres tables timeline :
    sans ça, purger un vieux patch laisserait ses achats orphelins en base pour
    toujours — les 28 Mo/jour du §6 ne se libéreraient jamais."""
    from lolcollector.prune import run_prune

    path = str(tmp_path / "prune.db")
    db = Database(path)
    try:
        for patch, match_id in (("16.15", "VIEUX_1"), ("16.16", "RECENT_1")):
            db.conn.execute(
                "INSERT INTO matches (match_id, region, platform, game_version,"
                " patch, game_duration, game_creation, tier_bucket_source,"
                " inserted_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (match_id, "europe", "euw1", f"{patch}.1.1", patch, 1800, 0,
                 "SILVER_GOLD", 0))
            db.conn.commit()
            store_timeline(db, match_id, timeline, legendary)
        assert db.conn.execute("SELECT COUNT(*) FROM item_events").fetchone()[0] > 0
    finally:
        db.close()

    exports = tmp_path / "etudes" / "tierlist" / "16-15"
    exports.mkdir(parents=True)
    (exports / "meta.json").write_text('{"patch": "16.15"}')
    assert run_prune(path, 1, str(tmp_path / "etudes"), assume_yes=True) == 0

    conn = sqlite3.connect(path)
    try:
        restants = {row[0] for row in conn.execute(
            "SELECT DISTINCT match_id FROM item_events")}
        assert restants == {"RECENT_1"}
    finally:
        conn.close()


def test_champ_assistants_absent_vaut_chaine_vide(timeline):
    """Constat sur la fixture réelle : Riot OMET `assistingParticipantIds` sur
    les solo kills (il n'envoie pas une liste vide). Traiter l'absence comme
    « non collecté » mettrait un tiers des kills à NULL et casserait la
    sémantique du §4."""
    bruts = [e for frame in timeline["info"]["frames"] for e in frame["events"]
             if e["type"] == "CHAMPION_KILL"]
    absents = [e for e in bruts if "assistingParticipantIds" not in e]
    assert absents, "la fixture ne porte plus de kill sans le champ"

    data = copy.deepcopy(timeline)
    events, _ = parse_timeline("EUW1_TL", data)
    kills = [e for e in events if e[2] == "CHAMPION_KILL"]
    sans_champ = [ligne for ligne, brut in zip(kills, bruts)
                  if "assistingParticipantIds" not in brut]
    assert sans_champ and all(e[ASSISTING] == "" for e in sans_champ)
