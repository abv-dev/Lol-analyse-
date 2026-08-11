"""Collecte des timelines Match-V5 (/timelines).

Permet les études temporelles : objectifs horodatés, or à la minute, side au
premier drake à or égal…

Échantillonnage : collecter la timeline de TOUS les matchs doublerait le coût
en requêtes et diviserait par deux le débit de matchs. On n'en prend donc
qu'une fraction (`TIMELINE_SAMPLE_RATE`, 0.33 par défaut), tirée de façon
**déterministe à partir du match_id** — pas de random : deux exécutions
retiennent exactement les mêmes matchs, et la rétro-collecte reste cohérente
avec la collecte en direct.
"""

import hashlib
import time

# Seuls ces types d'événements sont conservés (le reste — achats d'objets,
# level-ups, wards… — n'est pas exploité par les études prévues et
# multiplierait le volume).
KEPT_EVENT_TYPES = {
    "CHAMPION_KILL",
    "ELITE_MONSTER_KILL",
    "BUILDING_KILL",
    "TURRET_PLATE_DESTROYED",
}


def is_sampled(match_id: str, rate: float) -> bool:
    """Tirage déterministe et stable : hash du match_id ramené dans [0, 1).

    Reproductible d'une exécution à l'autre et d'une machine à l'autre
    (contrairement à hash() de Python, randomisé par PYTHONHASHSEED).
    """
    if rate >= 1:
        return True
    if rate <= 0:
        return False
    digest = hashlib.sha256(match_id.encode("utf-8")).digest()
    bucket = int.from_bytes(digest[:8], "big") / 2 ** 64
    return bucket < rate


def parse_timeline(match_id: str, data: dict):
    """Extrait (events, frames) d'une réponse Match-V5 /timelines."""
    info = data.get("info") or {}
    frames_in = info.get("frames") or []

    events = []
    frames = []
    for frame in frames_in:
        # timestamp de la frame -> minute entière
        minute = int(round((frame.get("timestamp") or 0) / 60000))
        for pid_str, pf in (frame.get("participantFrames") or {}).items():
            try:
                participant_id = int(pid_str)
            except (TypeError, ValueError):
                continue
            position = pf.get("position") or {}
            frames.append((
                match_id, minute, participant_id,
                pf.get("totalGold"), pf.get("currentGold"),
                pf.get("xp"), pf.get("level"),
                (pf.get("minionsKilled") or 0) + (pf.get("jungleMinionsKilled") or 0),
                position.get("x"), position.get("y"),
            ))

        for event in frame.get("events") or []:
            etype = event.get("type")
            if etype not in KEPT_EVENT_TYPES:
                continue
            position = event.get("position") or {}
            # teamId n'est pas toujours présent : BUILDING_KILL et
            # TURRET_PLATE_DESTROYED portent l'équipe PROPRIÉTAIRE de la
            # structure détruite, ce qui est l'information utile.
            events.append((
                match_id, event.get("timestamp"), etype, event.get("teamId"),
                event.get("killerId"), event.get("victimId"),
                event.get("monsterType"), event.get("monsterSubType"),
                event.get("laneType"), event.get("buildingType"),
                position.get("x"), position.get("y"),
            ))
    return events, frames


def store_timeline(db, match_id: str, data: dict) -> tuple[int, int]:
    """Stocke une timeline (transactionnel). Retourne (n_events, n_frames)."""
    events, frames = parse_timeline(match_id, data)
    cur = db.conn.cursor()
    try:
        cur.execute("BEGIN")
        cur.execute("DELETE FROM timeline_events WHERE match_id = ?", (match_id,))
        cur.execute("DELETE FROM timeline_frames WHERE match_id = ?", (match_id,))
        cur.executemany(
            "INSERT INTO timeline_events (match_id, timestamp_ms, type, team_id,"
            " killer_id, victim_id, monster_type, monster_subtype, lane_type,"
            " building_type, position_x, position_y)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", events)
        cur.executemany(
            "INSERT INTO timeline_frames (match_id, minute, participant_id,"
            " total_gold, current_gold, xp, level, cs, position_x, position_y)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)", frames)
        cur.execute(
            "INSERT OR REPLACE INTO timeline_state (match_id, status, fetched_at)"
            " VALUES (?,?,?)", (match_id, "ok", int(time.time())))
        db.conn.commit()
        return len(events), len(frames)
    except Exception:
        db.conn.rollback()
        raise


def mark_timeline(db, match_id: str, status: str) -> None:
    """Note qu'un match n'aura pas de timeline ('skipped' ou 'missing')."""
    db.conn.execute(
        "INSERT OR REPLACE INTO timeline_state (match_id, status, fetched_at)"
        " VALUES (?,?,?)", (match_id, status, int(time.time())))
    db.conn.commit()


def stored_count_for_patch(db, patch: str) -> int:
    """Timelines effectivement stockées pour un patch (statut 'ok')."""
    row = db.conn.execute(
        "SELECT COUNT(*) FROM timeline_state t"
        " JOIN matches m ON m.match_id = t.match_id"
        " WHERE m.patch = ? AND t.status = 'ok'", (patch,)
    ).fetchone()
    return row[0] if row else 0


class PatchQuota:
    """Plafond de timelines par patch, avec compteur mis en cache.

    Le comptage SQL n'est refait qu'au changement de patch ou tous les
    `refresh_every` stockages : sur une base de plusieurs Go, compter à chaque
    match coûterait plus cher que la collecte elle-même.
    """

    def __init__(self, db, target: int, refresh_every: int = 200):
        self.db = db
        self.target = target
        self.refresh_every = refresh_every
        self._patch: str | None = None
        self._count = 0
        self._since_refresh = 0

    def _sync(self, patch: str) -> None:
        self._patch = patch
        self._count = stored_count_for_patch(self.db, patch)
        self._since_refresh = 0

    def reached(self, patch: str) -> bool:
        if self.target <= 0:
            return False
        if patch != self._patch:
            # nouveau patch : les compteurs repartent de zéro
            self._sync(patch)
        elif self._since_refresh >= self.refresh_every:
            self._sync(patch)
        return self._count >= self.target

    def record_stored(self, patch: str) -> None:
        if patch == self._patch:
            self._count += 1
            self._since_refresh += 1


def has_timeline_state(db, match_id: str) -> bool:
    row = db.conn.execute(
        "SELECT 1 FROM timeline_state WHERE match_id = ?", (match_id,)).fetchone()
    return row is not None
