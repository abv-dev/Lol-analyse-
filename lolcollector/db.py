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
    position_y      INTEGER
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
"""

# Colonnes ajoutées après coup : appliquées à une base existante sans la
# recréer (la base de production fait plusieurs Go).
MIGRATIONS = [
    ("team_objectives", "horde_kills", "ALTER TABLE team_objectives ADD COLUMN horde_kills INTEGER"),
]


def patch_of(game_version: str) -> str:
    """'16.14.702.1234' -> '16.14'"""
    return ".".join((game_version or "").split(".")[:2])


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
        for table, column, sql in MIGRATIONS:
            existing = {
                row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                self.conn.execute(sql)

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
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN")
            cur.execute(
                "INSERT OR IGNORE INTO matches (match_id, region, platform, game_version,"
                " patch, game_duration, game_creation, tier_bucket_source, inserted_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (match_id, region, platform, game_version, patch,
                 info.get("gameDuration"), info.get("gameCreation"), bucket,
                 int(time.time())),
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
                cur.execute(
                    "INSERT INTO participants (match_id, puuid, champion_id, champion_name,"
                    " team_id, team_position, win, kills, deaths, assists,"
                    " item0, item1, item2, item3, item4, item5, item6,"
                    " perk_primary_style, perk_sub_style, perk_keystone,"
                    " gold_earned, total_cs, patch)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (match_id, part.get("puuid"), part.get("championId"),
                     part.get("championName"), part.get("teamId"),
                     part.get("teamPosition"), 1 if part.get("win") else 0,
                     part.get("kills"), part.get("deaths"), part.get("assists"),
                     part.get("item0"), part.get("item1"), part.get("item2"),
                     part.get("item3"), part.get("item4"), part.get("item5"),
                     part.get("item6"), primary_style, sub_style, keystone,
                     part.get("goldEarned"), total_cs, patch),
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
