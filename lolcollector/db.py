"""Stockage SQLite : schéma, dédup, insertion d'un match, état de sampling."""

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
    match_id            TEXT PRIMARY KEY,
    region              TEXT NOT NULL,
    platform            TEXT NOT NULL,
    game_version        TEXT,
    patch               TEXT,
    game_duration       INTEGER,
    game_creation       INTEGER,
    tier_bucket_source  TEXT,
    inserted_at         INTEGER,
    -- participants[0].gameEndedInEarlySurrender (identique pour les 10), 0/1
    early_surrender     INTEGER
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
    patch               TEXT,
    -- Dégâts, vision et fin de partie : info.participants[] de Match-V5.
    -- NULL = non collecté (match antérieur à la migration, ou champ absent
    -- du payload), 0 = mesuré nul. Ne jamais confondre les deux.
    damage_to_champions  INTEGER,   -- totalDamageDealtToChampions
    damage_taken         INTEGER,   -- totalDamageTaken
    vision_score         INTEGER,   -- visionScore
    control_wards_bought INTEGER,   -- visionWardsBoughtInGame
    time_spent_dead      INTEGER,   -- totalTimeSpentDead (secondes)
    -- Sous-objet challenges, absent de certains payloads : NULL dans ce cas.
    lane_cs_at10         INTEGER,   -- challenges.laneMinionsFirst10Minutes
    jungle_cs_at10       INTEGER,   -- challenges.jungleCsBefore10Minutes
    turret_plates_taken  INTEGER,   -- challenges.turretPlatesTaken
    heals_on_teammates   INTEGER,   -- totalHealsOnTeammates
    shields_on_teammates INTEGER,   -- totalDamageShieldedOnTeammates
    -- Identifiant Riot 1-10 (info.participants[].participantId) : seule clé
    -- de jointure vers les victim_id/participant_id des tables timeline.
    -- NULL = ligne antérieure au Lot 14 non encore backfillée.
    participant_id       INTEGER
);

CREATE TABLE IF NOT EXISTS bans (
    match_id    TEXT NOT NULL,
    team_id     INTEGER,
    champion_id INTEGER,
    pick_turn   INTEGER
);

CREATE TABLE IF NOT EXISTS team_objectives (
    match_id     TEXT NOT NULL,
    team_id      INTEGER,
    first_blood  INTEGER,
    first_tower  INTEGER,
    first_dragon INTEGER,
    first_baron  INTEGER,
    dragon_kills INTEGER,
    baron_kills  INTEGER,
    tower_kills  INTEGER,
    herald_kills INTEGER,   -- objectives.riftHerald : Rift Herald UNIQUEMENT
    horde_kills  INTEGER,   -- objectives.horde : voidgrubs (larves du Néant)
    PRIMARY KEY (match_id, team_id)
);

-- Timelines (Match-V5 /timelines), échantillonnées : voir TIMELINE_SAMPLE_RATE.
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
    position_y      INTEGER,
    -- CHAMPION_KILL uniquement : assistingParticipantIds sérialisé "6,8,10",
    -- dans l'ordre du payload. '' = kill mesuré SANS assistant, NULL = non
    -- collecté (ligne antérieure au Lot 14, ou autre type d'événement). Ne
    -- jamais confondre les deux : les lots aval excluent les NULL de cette
    -- métrique seulement.
    assisting_ids   TEXT
);

-- Achats (et annulations) d'objets légendaires COMPLÉTÉS, extraits des mêmes
-- timelines que ci-dessus. Table dédiée à index unique : conserver tous les
-- ITEM_PURCHASED dans timeline_events coûterait 13x plus (spec Lot 14, §6).
CREATE TABLE IF NOT EXISTS item_events (
    match_id       TEXT NOT NULL,
    participant_id INTEGER,
    item_id        INTEGER,   -- itemId (achat) ou beforeId (annulation)
    timestamp_ms   INTEGER,
    event          TEXT       -- 'PURCHASED' | 'UNDO'
);

-- Traçabilité du filtre « légendaire » appliqué, patch par patch : une étude
-- aval reconstitue exactement la liste utilisée le jour de la collecte. La
-- version Data Dragon d'où elle sort est dans meta ('legendary_ddragon_<patch>').
CREATE TABLE IF NOT EXISTS legendary_items (
    patch   TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    PRIMARY KEY (patch, item_id)
);

CREATE TABLE IF NOT EXISTS timeline_frames (
    match_id       TEXT NOT NULL,
    minute         INTEGER,
    participant_id INTEGER,
    total_gold     INTEGER,
    current_gold   INTEGER,
    xp             INTEGER,
    level          INTEGER,
    cs             INTEGER,
    position_x     INTEGER,
    position_y     INTEGER
);

-- Matchs dont la timeline a été traitée : évite de re-dépenser une requête.
-- status : 'ok' (stockée), 'skipped' (hors échantillon), 'missing' (404 Riot).
CREATE TABLE IF NOT EXISTS timeline_state (
    match_id    TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    fetched_at  INTEGER
);

CREATE TABLE IF NOT EXISTS sampling_state (
    region   TEXT NOT NULL,
    bucket   TEXT NOT NULL,
    tier_idx INTEGER NOT NULL,
    div_idx  INTEGER NOT NULL,
    page     INTEGER NOT NULL,
    PRIMARY KEY (region, bucket)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE INDEX IF NOT EXISTS idx_matches_patch_bucket
    ON matches (patch, tier_bucket_source);
CREATE INDEX IF NOT EXISTS idx_participants_champ_patch
    ON participants (champion_id, patch);
CREATE INDEX IF NOT EXISTS idx_participants_match
    ON participants (match_id);
CREATE INDEX IF NOT EXISTS idx_bans_match
    ON bans (match_id);
CREATE INDEX IF NOT EXISTS idx_matches_inserted
    ON matches (inserted_at);

-- Index pensés pour les requêtes d'étude temporelle
CREATE INDEX IF NOT EXISTS idx_tl_frames_match_minute
    ON timeline_frames (match_id, minute);
CREATE INDEX IF NOT EXISTS idx_tl_events_match_type
    ON timeline_events (match_id, type);
CREATE INDEX IF NOT EXISTS idx_tl_events_type_ts
    ON timeline_events (type, timestamp_ms);
CREATE INDEX IF NOT EXISTS idx_tl_state_status
    ON timeline_state (status);
CREATE INDEX IF NOT EXISTS idx_item_events_match
    ON item_events (match_id);
"""

# Lot 13 : dégâts, vision et métriques de fin de partie. Isolées du reste pour
# la borne temporelle ci-dessous ; sinon migrations ordinaires.
LOT13_MIGRATIONS = [
    ("participants", "damage_to_champions", "ALTER TABLE participants ADD COLUMN damage_to_champions INTEGER"),
    ("participants", "damage_taken", "ALTER TABLE participants ADD COLUMN damage_taken INTEGER"),
    ("participants", "vision_score", "ALTER TABLE participants ADD COLUMN vision_score INTEGER"),
    ("participants", "control_wards_bought", "ALTER TABLE participants ADD COLUMN control_wards_bought INTEGER"),
    ("participants", "time_spent_dead", "ALTER TABLE participants ADD COLUMN time_spent_dead INTEGER"),
    ("participants", "lane_cs_at10", "ALTER TABLE participants ADD COLUMN lane_cs_at10 INTEGER"),
    ("participants", "jungle_cs_at10", "ALTER TABLE participants ADD COLUMN jungle_cs_at10 INTEGER"),
    ("participants", "turret_plates_taken", "ALTER TABLE participants ADD COLUMN turret_plates_taken INTEGER"),
    ("participants", "heals_on_teammates", "ALTER TABLE participants ADD COLUMN heals_on_teammates INTEGER"),
    ("participants", "shields_on_teammates", "ALTER TABLE participants ADD COLUMN shields_on_teammates INTEGER"),
    ("matches", "early_surrender", "ALTER TABLE matches ADD COLUMN early_surrender INTEGER"),
]

# Lot 14 : identifiant Riot du participant et assistants des kills. Les deux
# colonnes s'ajoutent en fin de schéma, hors de tout index : les 5,5 M de
# lignes timeline_events existantes ne sont pas réécrites.
LOT14_MIGRATIONS = [
    ("participants", "participant_id", "ALTER TABLE participants ADD COLUMN participant_id INTEGER"),
    ("timeline_events", "assisting_ids", "ALTER TABLE timeline_events ADD COLUMN assisting_ids TEXT"),
]

# Colonnes ajoutées après coup : appliquées à une base existante sans la
# recréer (la base de production fait plusieurs Go).
MIGRATIONS = [
    ("team_objectives", "horde_kills", "ALTER TABLE team_objectives ADD COLUMN horde_kills INTEGER"),
] + LOT13_MIGRATIONS + LOT14_MIGRATIONS

# Borne temporelle des NULL du Lot 13 : posée une seule fois, au premier ALTER
# réellement appliqué. Avant elle, les colonnes sont NULL pour toujours ; les
# consommateurs bornent par matches.inserted_at plutôt que de scanner.
LOT13_BORNE = "lot13_migrated_at"
_LOT13_COLUMNS = {(table, column) for table, column, _ in LOT13_MIGRATIONS}


def patch_of(game_version: str) -> str:
    """'16.14.702.1234' -> '16.14'"""
    return ".".join((game_version or "").split(".")[:2])


def patch_sort_key(patch: str) -> tuple[int, int]:
    """Clé de tri numérique d'un patch : (16, 9) < (16, 15) < (16, 16).

    Le tri lexicographique de SQLite est faux ici — « 16.9 » y passe après
    « 16.16 », ce qui ferait traiter les vieux patchs avant les récents.

    Un patch au format inattendu (« PBE », « 16 » sans mineur, valeur non
    numérique) prend (-1, -1) : il reste traité, mais après tous les patchs
    valides en ordre décroissant, plutôt que de faire échouer le tri.
    """
    parts = str(patch).split(".")
    if len(parts) < 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        return (-1, -1)
    return (int(parts[0]), int(parts[1]))


# ---- plancher de collecte : ne rien collecter d'antérieur au patch courant ----
#
# Sans lui, une base purgée se re-remplit : le collecteur demande 28 jours
# d'historique par joueur et ne saute que les matchs DÉJÀ en base, donc il
# retéléchargerait un à un tous les matchs qu'on vient de supprimer.

# Posé par la purge : patch courant au moment de la reconstruction. Le patch
# plancher est le plus récent de celui-ci et du patch Data Dragon courant.
PURGE_MIN_PATCH = "purge_min_patch"


def collect_since_key(region: str) -> str:
    """Clé meta de la borne startTime (epoch secondes) d'une région."""
    return f"collect_since_s_{region}"


def collect_floor_patch(db) -> str | None:
    """Patch en dessous duquel un match n'est plus inséré (None : aucun)."""
    candidates = [patch_of(db.get_meta("ddragon_current") or ""),
                  db.get_meta(PURGE_MIN_PATCH) or ""]
    valid = [p for p in candidates if patch_sort_key(p) != (-1, -1)]
    return max(valid, key=patch_sort_key) if valid else None


def collect_since(db, region: str) -> int:
    """Borne basse (epoch secondes) de la liste de matchs d'une région, 0 si
    aucune."""
    try:
        return int(db.get_meta(collect_since_key(region)))
    except (TypeError, ValueError):
        return 0


def match_end_s(info: dict) -> int | None:
    """Fin de partie en epoch secondes (gameEndTimestamp, sinon création +
    durée). None si le payload ne permet pas de la dater."""
    end = info.get("gameEndTimestamp")
    if end is None:
        creation = info.get("gameCreation")
        if creation is None:
            return None
        end = creation + (info.get("gameDuration") or 0) * 1000
    return int(end) // 1000


def raise_collect_since(db, region: str, seconds: int) -> None:
    """Remonte la borne d'une région, sans jamais la baisser ni la placer
    dans le futur (une borne future bloquerait toute collecte)."""
    seconds = min(int(seconds), int(time.time()))
    if seconds > collect_since(db, region):
        db.set_meta(collect_since_key(region), str(seconds))


def is_before_collect_floor(db, region: str, data: dict) -> bool:
    """Vrai si le match appartient à un patch antérieur au plancher : il ne
    doit pas être inséré.

    Effet de bord voulu : la borne startTime de la région remonte juste après
    la fin de ce match. Sur une plateforme, toutes les parties d'un patch se
    terminent avant la première du suivant (serveurs coupés au déploiement) :
    la borne converge en quelques rejets vers le déploiement, et les matchs
    rejetés ne sont plus jamais redemandés à Riot.
    """
    floor = collect_floor_patch(db)
    if floor is None:
        return False
    info = data.get("info") or {}
    if patch_sort_key(patch_of(info.get("gameVersion", ""))) >= patch_sort_key(floor):
        return False
    end = match_end_s(info)
    if end is not None:
        raise_collect_since(db, region, end + 1)
    return True


class ParticipantIdError(Exception):
    """Garde-fou du backfill : la dérivation du participant_id n'a pas tenu."""


def check_participant_ids(conn, limit: int = 10) -> tuple[int, list[str]]:
    """Matchs à 10 participants qui ne portent PAS exactement les ids 1-10.

    Retourne (nombre total, échantillon de `limit` match_id). Les matchs à
    moins de 10 lignes ne sont pas contrôlés (§2.2 de la spec) : la propriété
    « rang = participantId » ne se vérifie que sur un match complet.
    """
    incoherents = (
        "SELECT match_id FROM participants"
        " GROUP BY match_id HAVING COUNT(*) = 10 AND ("
        "     COUNT(DISTINCT participant_id) <> 10"
        "  OR MIN(participant_id) <> 1"
        "  OR MAX(participant_id) <> 10)"
    )
    total = conn.execute(
        f"SELECT COUNT(*) FROM ({incoherents})").fetchone()[0]
    exemples = [row[0] for row in conn.execute(f"{incoherents} LIMIT {int(limit)}")]
    return total, exemples


def backfill_participant_ids(conn) -> int:
    """Renseigne participant_id sur les lignes historiques, en un seul UPDATE.

    Dérivation validée sur la base réelle (§2.2 de la spec Lot 14) : le rang
    d'insertion des lignes d'un match vaut le participantId Riot, parce que
    `store_match` insère les 10 participants dans l'ordre du payload et que
    Match-V5 les renvoie ordonnés de 1 à 10.

    Seules les lignes à NULL sont touchées. Le garde-fou tourne dans la MÊME
    transaction : s'il déclenche, l'UPDATE est intégralement annulé (y compris
    les matchs sains) et l'appelant sort en erreur, base inchangée.

    À exécuter collecteur arrêté, jamais pendant la collecte.
    """
    try:
        conn.execute("BEGIN")
        cur = conn.execute(
            "UPDATE participants SET participant_id = rangs.rang"
            " FROM (SELECT rowid AS rid,"
            "              ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY rowid)"
            "                  AS rang"
            "       FROM participants) AS rangs"
            " WHERE participants.rowid = rangs.rid"
            "   AND participants.participant_id IS NULL"
        )
        modifiees = cur.rowcount
        total, exemples = check_participant_ids(conn)
        if total:
            conn.rollback()
            raise ParticipantIdError(
                f"{total} match(s) ne portent pas exactement les participant_id "
                f"1-10 après backfill (ex. : {', '.join(exemples)}) — "
                "aucune écriture conservée")
        conn.commit()
        return modifiees
    except Exception:
        conn.rollback()
        raise


def challenge_int(value):
    """Champ `challenges` -> entier. Ces valeurs sont calculées côté Riot et
    arrivent parfois en flottant (`jungleCsBefore10Minutes` à
    68.00000008940697) : sans conversion, la colonne mélange INTEGER et REAL.

    Arrondi et non troncature — l'artefact peut tomber des deux côtés de
    l'entier vrai (67.99999991 comme 68.00000009), et tronquer biaiserait
    systématiquement vers le bas. NULL reste NULL : la conversion ne
    s'applique jamais à une valeur absente et ne produit jamais de 0.
    """
    return None if value is None else int(round(value))


class Database:
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Applique les colonnes ajoutées après coup (base existante)."""
        lot13_applied = False
        for table, column, sql in MIGRATIONS:
            existing = {
                row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                self.conn.execute(sql)
                if (table, column) in _LOT13_COLUMNS:
                    lot13_applied = True
        # Sur une base neuve, SCHEMA a déjà créé les colonnes : aucun ALTER, donc
        # pas de borne à poser — il n'y a pas de lignes antérieures.
        if lot13_applied and self.get_meta(LOT13_BORNE) is None:
            self.set_meta(LOT13_BORNE, str(int(time.time())))

    def close(self):
        self.conn.close()

    # ---- dédup : vérifié AVANT de dépenser la requête de détail Match-V5 ----

    def has_match(self, match_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM matches WHERE match_id = ?", (match_id,)
        ).fetchone()
        return row is not None

    # ---- insertion d'un match complet (transactionnelle) ----

    def store_match(self, data: dict, region: str, platform: str, bucket: str) -> bool:
        """Insère un match Match-V5. Retourne False si déjà présent ou hors queue 420."""
        info = data.get("info") or {}
        metadata = data.get("metadata") or {}
        match_id = metadata.get("matchId")
        if not match_id or info.get("queueId") != 420:
            return False

        game_version = info.get("gameVersion", "")
        patch = patch_of(game_version)
        # gameEndedInEarlySurrender est identique pour les 10 participants :
        # valeur de match, stockée une fois. NULL si le champ manque.
        participants = info.get("participants") or []
        early = participants[0].get("gameEndedInEarlySurrender") if participants else None
        early_surrender = None if early is None else (1 if early else 0)
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute(
                "INSERT OR IGNORE INTO matches (match_id, region, platform, game_version,"
                " patch, game_duration, game_creation, tier_bucket_source, inserted_at,"
                " early_surrender)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (match_id, region, platform, game_version, patch,
                 info.get("gameDuration"), info.get("gameCreation"), bucket,
                 int(time.time()), early_surrender),
            )
            if cur.rowcount == 0:  # déjà en base (course entre workers improbable mais sûre)
                self.conn.rollback()
                return False

            for part in info.get("participants", []):
                perks = part.get("perks") or {}
                primary_style = sub_style = keystone = None
                for style in perks.get("styles", []):
                    if style.get("description") == "primaryStyle":
                        primary_style = style.get("style")
                        selections = style.get("selections") or []
                        if selections:
                            keystone = selections[0].get("perk")
                    elif style.get("description") == "subStyle":
                        sub_style = style.get("style")
                total_cs = (part.get("totalMinionsKilled", 0) or 0) + \
                           (part.get("neutralMinionsKilled", 0) or 0)
                # challenges manque sur certains matchs : ses champs valent
                # alors NULL — non collecté, à ne pas coalescer à 0.
                challenges = part.get("challenges") or {}
                cur.execute(
                    "INSERT INTO participants (match_id, puuid, champion_id, champion_name,"
                    " team_id, team_position, win, kills, deaths, assists,"
                    " item0, item1, item2, item3, item4, item5, item6,"
                    " perk_primary_style, perk_sub_style, perk_keystone,"
                    " gold_earned, total_cs, patch,"
                    " damage_to_champions, damage_taken, vision_score,"
                    " control_wards_bought, time_spent_dead, lane_cs_at10,"
                    " jungle_cs_at10, turret_plates_taken, heals_on_teammates,"
                    " shields_on_teammates, participant_id)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
                    "?,?,?,?,?,?,?,?,?,?,?)",
                    (match_id, part.get("puuid"), part.get("championId"),
                     part.get("championName"), part.get("teamId"),
                     part.get("teamPosition"), 1 if part.get("win") else 0,
                     part.get("kills"), part.get("deaths"), part.get("assists"),
                     part.get("item0"), part.get("item1"), part.get("item2"),
                     part.get("item3"), part.get("item4"), part.get("item5"),
                     part.get("item6"), primary_style, sub_style, keystone,
                     part.get("goldEarned"), total_cs, patch,
                     part.get("totalDamageDealtToChampions"),
                     part.get("totalDamageTaken"), part.get("visionScore"),
                     part.get("visionWardsBoughtInGame"),
                     part.get("totalTimeSpentDead"),
                     challenge_int(challenges.get("laneMinionsFirst10Minutes")),
                     challenge_int(challenges.get("jungleCsBefore10Minutes")),
                     challenge_int(challenges.get("turretPlatesTaken")),
                     part.get("totalHealsOnTeammates"),
                     part.get("totalDamageShieldedOnTeammates"),
                     # toujours présent dans Match-V5 : NULL ici signalerait un
                     # payload cassé, pas une ligne « non collectée »
                     part.get("participantId")),
                )

            for team in info.get("teams", []):
                team_id = team.get("teamId")
                for ban in team.get("bans", []):
                    cur.execute(
                        "INSERT INTO bans (match_id, team_id, champion_id, pick_turn)"
                        " VALUES (?,?,?,?)",
                        (match_id, team_id, ban.get("championId"), ban.get("pickTurn")),
                    )
                obj = team.get("objectives") or {}

                def o(name, field):
                    entry = obj.get(name) or {}
                    value = entry.get(field)
                    if field == "first":
                        return 1 if value else 0
                    return value or 0

                # riftHerald = Rift Herald ; horde = voidgrubs. Deux objectifs
                # distincts dans Match-V5, à ne surtout pas confondre.
                cur.execute(
                    "INSERT OR REPLACE INTO team_objectives (match_id, team_id,"
                    " first_blood, first_tower, first_dragon, first_baron,"
                    " dragon_kills, baron_kills, tower_kills, herald_kills,"
                    " horde_kills)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (match_id, team_id,
                     o("champion", "first"), o("tower", "first"),
                     o("dragon", "first"), o("baron", "first"),
                     o("dragon", "kills"), o("baron", "kills"),
                     o("tower", "kills"), o("riftHerald", "kills"),
                     o("horde", "kills")),
                )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    # ---- curseurs de sampling persistés (reprise après crash) ----

    def load_cursor(self, region: str, bucket: str):
        row = self.conn.execute(
            "SELECT tier_idx, div_idx, page FROM sampling_state"
            " WHERE region = ? AND bucket = ?", (region, bucket)
        ).fetchone()
        return row  # None si premier lancement

    def save_cursor(self, region: str, bucket: str, tier_idx: int, div_idx: int, page: int):
        self.conn.execute(
            "INSERT OR REPLACE INTO sampling_state (region, bucket, tier_idx, div_idx, page)"
            " VALUES (?,?,?,?,?)",
            (region, bucket, tier_idx, div_idx, page),
        )
        self.conn.commit()

    # ---- meta (version ddragon courante) ----

    def get_meta(self, key: str):
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value)
        )
        self.conn.commit()
