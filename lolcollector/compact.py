"""Purge au changement de patch : la base ne garde que le patch courant.

    python3 collector.py purge-old-patches [--patch X.Y] [--dry-run] [--yes]
                                           [--no-export] [--min-games 200]

Lancée toutes les heures par cron (`--yes`) : sans changement de patch, elle
constate qu'il n'y a rien à purger et sort en une seconde, sans toucher au
collecteur.

Pourquoi reconstruire plutôt que DELETE + VACUUM : VACUUM réécrit la base
entière dans un fichier temporaire, il lui faut jusqu'à deux fois sa taille
en espace libre. C'est ce qui a rendu l'ancien `prune` inutilisable le jour
où le disque a saturé (38 Go de base, 2,5 Go libres). Ici une base neuve ne
reçoit que les lignes des patchs gardés, puis remplace l'ancienne : l'espace
nécessaire est celui de ce qu'on garde, pas de ce qu'on jette.

Déroulé, collecteur arrêté (et redémarré quoi qu'il arrive s'il tournait) :

1. export tierlist du patch sortant vers site/data/etudes (option B du
   2026-09-15) — NON bloquant : un export refusé ou en échec est journalisé,
   la purge continue ;
2. reconstruction dans `<base>.rebuild` : schéma et migrations du collecteur
   (`Database`), lignes des patchs gardés recopiées avec leur rowid (le rang
   d'insertion porte participant_id), tables d'état recopiées en entier ;
3. vérifications : integrity_check, comptes identiques table par table,
   aucune ligne orpheline, réouverture par `Database` ;
4. bascule atomique (`os.replace`) : l'ancienne base disparaît avec elle.

Les études publiées ne dépendent que des JSON exportés, jamais de la base.
"""

import fcntl
import json
import os
import shutil
import sqlite3
import time
import urllib.request
from urllib.parse import quote

from .db import (
    PURGE_MIN_PATCH,
    Database,
    collect_since_key,
    patch_of,
    patch_sort_key,
)
from .export import export_tierlist
from .refresh import _script, collector_running

DDRAGON_VERSIONS_URL = "https://ddragon.leagueoflegends.com/api/versions.json"

# Tables rattachées à un match par match_id : elles suivent le sort du match.
MATCH_TABLES = ("participants", "bans", "team_objectives", "timeline_events",
                "timeline_frames", "timeline_state", "item_events")
# Tables d'état, sans patch : recopiées en entier.
STATE_TABLES = ("meta", "sampling_state", "legendary_items")
KNOWN_TABLES = {"matches", *MATCH_TABLES, *STATE_TABLES}

SAFETY_MARGIN = 500 * 1024 ** 2
# Création des index d'export sur la base sortante : ~ un quart de sa taille.
EXPORT_SPACE_RATIO = 0.25


class PurgeRefused(Exception):
    """Garde-fou : rien n'a été modifié."""


def say(message: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}", flush=True)


def free_bytes(path: str) -> int:
    return shutil.disk_usage(os.path.dirname(os.path.abspath(path))).free


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.2f} Go"


def _uri(path: str, **params) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"file:{quote(os.path.abspath(path))}" + (f"?{query}" if query else "")


def _reader(path: str) -> sqlite3.Connection:
    """Connexion de lecture. Pas `mode=ro` : sur une base WAL, une connexion
    en lecture seule crée -wal/-shm sans pouvoir les supprimer en sortant."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA query_only=ON")
    return conn


def detect_current_patch(db_path: str) -> str | None:
    """Patch courant : Data Dragon, sinon la version vue par le collecteur."""
    try:
        request = urllib.request.Request(DDRAGON_VERSIONS_URL,
                                         headers={"User-Agent": "lol-studies-collector"})
        with urllib.request.urlopen(request, timeout=30) as resp:
            versions = json.loads(resp.read().decode("utf-8"))
        return patch_of(versions[0])
    except Exception as exc:  # noqa: BLE001 — réseau, JSON, liste vide
        say(f"! Data Dragon injoignable ({exc}), repli sur la base")
    conn = _reader(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM meta WHERE key = 'ddragon_current'").fetchone()
    finally:
        conn.close()
    return patch_of(row[0]) if row else None


def open_handles(path: str) -> list[int]:
    """PID des autres processus qui ont la base (ou son WAL) ouverte."""
    targets = {os.path.realpath(path + suffix) for suffix in ("", "-wal", "-journal")}
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        fd_dir = f"/proc/{entry}/fd"
        try:
            for fd in os.listdir(fd_dir):
                if os.path.realpath(os.path.join(fd_dir, fd)) in targets:
                    pids.append(int(entry))
                    break
        except OSError:
            continue  # processus disparu ou d'un autre utilisateur
    return pids


def wait_until_closed(path: str, timeout: float = 60) -> None:
    """milestone_check ouvre la base quelques secondes chaque heure : on
    patiente plutôt que d'échouer ; au-delà, refus."""
    deadline = time.time() + timeout
    while True:
        pids = open_handles(path)
        if not pids:
            return
        if time.time() >= deadline:
            raise PurgeRefused(f"base ouverte par les processus {pids}")
        time.sleep(2)


def _tables(conn: sqlite3.Connection, schema: str = "main") -> set[str]:
    return {name for (name,) in conn.execute(
        f"SELECT name FROM {schema}.sqlite_master"
        " WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def plan(db_path: str, current: str) -> dict:
    """Répartition par patch et partage gardé / purgé. Lecture seule."""
    conn = _reader(db_path)
    try:
        unknown = _tables(conn) - KNOWN_TABLES
        counts = dict(conn.execute(
            "SELECT patch, COUNT(*) FROM matches GROUP BY patch").fetchall())
    finally:
        conn.close()
    floor = patch_sort_key(current)
    kept = sorted((p for p in counts if p is not None
                   and patch_sort_key(p) >= floor), key=patch_sort_key)
    old = sorted((p for p in counts if p not in kept),
                 key=lambda p: patch_sort_key(p or ""))
    total = sum(counts.values())
    kept_matches = sum(counts[p] for p in kept)
    size = os.path.getsize(db_path)
    return {
        "current": current, "counts": counts, "kept": kept, "old": old,
        "unknown_tables": sorted(unknown), "total_matches": total,
        "kept_matches": kept_matches, "db_size": size,
        # proportionnel aux matchs gardés, +10 % : les index d'export, qui ne
        # sont pas recopiés, compensent largement l'inégale répartition des
        # timelines entre patchs
        "estimated_size": int(size * kept_matches / total * 1.1) if total else 0,
    }


def rebuild(db_path: str, tmp_path: str, kept: list[str],
            current: str) -> dict[str, int]:
    """Construit tmp_path avec les seuls patchs `kept` (ceux ≥ `current`).
    Retourne les comptes par table. Lève PurgeRefused si le schéma source ne
    se laisse pas recopier sans perte."""
    remove_db_files(tmp_path)
    Database(tmp_path).close()  # schéma + migrations du collecteur, à l'identique

    dst = sqlite3.connect(_uri(tmp_path), uri=True)
    try:
        dst.execute("PRAGMA journal_mode=DELETE")
        dst.execute("PRAGMA synchronous=OFF")
        dst.execute("ATTACH DATABASE ? AS src", (_uri(db_path, mode="ro"),))
        kept_json = json.dumps(kept)
        dst.execute("CREATE TEMP TABLE keep_ids (match_id TEXT PRIMARY KEY)")
        dst.execute("INSERT INTO keep_ids SELECT match_id FROM src.matches"
                    " WHERE patch IN (SELECT value FROM json_each(?))", (kept_json,))

        def copy(table: str, where: str, params=(), keep_rowid: bool = True):
            src_cols = _columns(dst, "src", table)
            dst_cols = _columns(dst, "main", table)
            lost = [c for c in src_cols if c not in dst_cols]
            if lost:
                raise PurgeRefused(f"colonnes de {table} inconnues du schéma "
                                   f"du collecteur, elles seraient perdues : {lost}")
            cols = ", ".join(f'"{c}"' for c in dst_cols if c in src_cols)
            rowid = "rowid, " if keep_rowid else ""
            dst.execute(f"INSERT OR REPLACE INTO main.{table} ({rowid}{cols})"
                        f" SELECT {rowid}{cols} FROM src.{table} {where}", params)

        copy("matches", "WHERE patch IN (SELECT value FROM json_each(?))", (kept_json,))
        for table in MATCH_TABLES:
            copy(table, "WHERE match_id IN (SELECT match_id FROM temp.keep_ids)")
        for table in STATE_TABLES:
            copy(table, "", keep_rowid=False)

        # Plancher de collecte, par région : juste après la dernière partie
        # des patchs purgés, sans jamais exclure une partie d'un patch gardé.
        dst.execute("INSERT OR REPLACE INTO main.meta (key, value) VALUES (?, ?)",
                    (PURGE_MIN_PATCH, current))
        bounds = dst.execute(
            "SELECT region,"
            "  MAX(CASE WHEN keep.match_id IS NULL"
            "      THEN m.game_creation + COALESCE(m.game_duration, 0) * 1000 END),"
            "  MIN(CASE WHEN keep.match_id IS NOT NULL THEN m.game_creation END)"
            # NOT INDEXED : sinon parcours de idx_matches_export avec un accès
            # aléatoire à la table par ligne, au lieu d'un scan séquentiel
            " FROM src.matches AS m NOT INDEXED"
            " LEFT JOIN temp.keep_ids keep USING (match_id)"
            " GROUP BY region").fetchall()
        now = int(time.time())
        for region, old_end_ms, kept_start_ms in bounds:
            if old_end_ms is None:
                continue
            since = old_end_ms // 1000 + 1
            if kept_start_ms is not None:
                since = min(since, kept_start_ms // 1000)
            since = min(since, now)
            row = dst.execute("SELECT value FROM main.meta WHERE key = ?",
                              (collect_since_key(region),)).fetchone()
            previous = int(row[0]) if row and str(row[0]).isdigit() else 0
            dst.execute("INSERT OR REPLACE INTO main.meta (key, value) VALUES (?, ?)",
                        (collect_since_key(region), str(max(since, previous))))
        dst.commit()
        counts = verify(dst)
        dst.execute("DETACH DATABASE src")
    finally:
        dst.close()

    # Le collecteur doit pouvoir l'ouvrir tel quel (schéma, migrations, WAL).
    Database(tmp_path).close()
    # Mode WAL lu dans l'en-tête (octets 18-19 = 2) : ouvrir une connexion
    # pour le demander recréerait les fichiers qu'on vérifie juste après.
    with open(tmp_path, "rb") as fh:
        header = fh.read(20)
    if header[18:20] != b"\x02\x02":
        raise PurgeRefused("base reconstruite hors WAL (en-tête "
                           f"{header[18]}/{header[19]})")
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.exists(tmp_path + suffix):
            raise PurgeRefused(f"{tmp_path}{suffix} subsiste après fermeture")
    return counts


def verify(dst: sqlite3.Connection) -> dict[str, int]:
    """Contrôles sur la base reconstruite (src encore attachée)."""
    result = dst.execute("PRAGMA main.integrity_check").fetchall()
    if result != [("ok",)]:
        raise PurgeRefused(f"integrity_check : {result[:5]}")
    counts = {}
    for table in ("matches", *MATCH_TABLES, *STATE_TABLES):
        where = ("" if table in STATE_TABLES
                 else "WHERE match_id IN (SELECT match_id FROM temp.keep_ids)")
        expected = dst.execute(f"SELECT COUNT(*) FROM src.{table} {where}").fetchone()[0]
        # meta : la purge y ajoute purge_min_patch et les planchers de région
        got = dst.execute(
            "SELECT COUNT(*) FROM main.meta WHERE key IN (SELECT key FROM src.meta)"
            if table == "meta" else f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]
        if got != expected:
            raise PurgeRefused(f"{table} : {got} lignes recopiées, {expected} attendues")
        counts[table] = got
    for table in MATCH_TABLES:
        orphans = dst.execute(
            f"SELECT COUNT(*) FROM main.{table} WHERE match_id NOT IN"
            " (SELECT match_id FROM main.matches)").fetchone()[0]
        if orphans:
            raise PurgeRefused(f"{table} : {orphans} lignes orphelines")
    return counts


def remove_db_files(path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


def swap(db_path: str, tmp_path: str) -> None:
    """Remplace la base. Un WAL de l'ancienne base resté à côté serait REJOUÉ
    sur la nouvelle : on le vide et on refuse s'il subsiste."""
    if os.path.exists(db_path + "-journal"):
        raise PurgeRefused(f"{db_path}-journal présent (transaction interrompue "
                           "sur l'ancienne base) — à examiner à la main")
    if os.path.exists(db_path + "-wal"):
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    if os.path.exists(db_path + "-wal"):
        raise PurgeRefused(f"{db_path}-wal subsiste après checkpoint")
    try:
        os.remove(db_path + "-shm")
    except FileNotFoundError:
        pass
    os.replace(tmp_path, db_path)


def export_outgoing(db_path: str, patch: str, min_games: int) -> None:
    """Option B : export tierlist du patch sortant, jamais bloquant."""
    needed = int(os.path.getsize(db_path) * EXPORT_SPACE_RATIO) + SAFETY_MARGIN
    if free_bytes(db_path) < needed:
        say(f"! export {patch} ignoré : {_gb(free_bytes(db_path))} libres, "
            f"~{_gb(needed)} nécessaires pour ses index")
        return
    say(f"→ Export tierlist du patch sortant {patch}…")
    try:
        export_tierlist(db_path, patch, None, min_games=min_games)
    except SystemExit as exc:  # ExportRefused et refus d'export
        say(f"! export {patch} non écrit : {exc} — la purge continue")
    except Exception as exc:  # noqa: BLE001 — jamais bloquant
        say(f"! export {patch} en échec : {type(exc).__name__}: {exc} — "
            "la purge continue")


def run_purge(cfg, patch: str | None = None, dry_run: bool = False,
              assume_yes: bool = False, export: bool = True,
              min_games: int = 200) -> int:
    db_path = cfg.db_path
    if not os.path.exists(db_path):
        say(f"Base introuvable : {db_path}")
        return 1
    lock = open(os.path.join(os.path.dirname(os.path.abspath(db_path)),
                             ".purge.lock"), "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        say("Une purge est déjà en cours, rien à faire.")
        return 0
    try:
        return _run_locked(cfg, db_path, patch, dry_run, assume_yes, export, min_games)
    finally:
        lock.close()


def _run_locked(cfg, db_path, patch, dry_run, assume_yes, export, min_games) -> int:
    current = patch or detect_current_patch(db_path)
    if not current or patch_sort_key(current) == (-1, -1):
        say(f"Patch courant indéterminé ({current!r}) : rien n'est purgé.")
        return 1
    p = plan(db_path, current)
    if not p["old"]:
        say(f"Patch courant {current} : aucun patch antérieur en base, rien à purger.")
        return 0

    say(f"Patch courant    : {current}")
    say("Patchs gardés    : " + (", ".join(f"{k} ({p['counts'][k]})" for k in p["kept"])
                               or "aucun (base repartant vide)"))
    say(f"Patchs purgés    : {len(p['old'])} — "
        + ", ".join(f"{k} ({p['counts'][k]})" for k in p["old"][-4:])
        + (" …" if len(p["old"]) > 4 else ""))
    say(f"Base actuelle    : {_gb(p['db_size'])}, {p['total_matches']} matchs ; "
        f"estimée après : {_gb(p['estimated_size'])} ; libre : {_gb(free_bytes(db_path))}")
    outgoing = p["old"][-1]
    say(f"Export préalable : {'tierlist ' + outgoing if export else 'désactivé (--no-export)'}")
    if p["unknown_tables"]:
        say(f"REFUS : tables inconnues du collecteur, elles seraient perdues : "
            f"{p['unknown_tables']}")
        return 1
    if dry_run:
        say("--dry-run : rien n'a été modifié.")
        return 0
    if not assume_yes:
        answer = input("Confirmer la reconstruction ? [oui/NON] ").strip().lower()
        if answer not in ("oui", "o", "yes", "y"):
            say("Annulé, rien n'a été modifié.")
            return 0

    was_running = collector_running(cfg)
    if was_running:
        say("→ Arrêt du collecteur…")
        if _script("stop.sh") != 0:
            say("! stop.sh a échoué, purge annulée (le collecteur tourne toujours).")
            return 1
    tmp_path = db_path + ".rebuild"
    rc = 1
    try:
        wait_until_closed(db_path)
        if export:
            export_outgoing(db_path, outgoing, min_games)
        p = plan(db_path, current)  # l'export a pu ajouter des index
        needed = p["estimated_size"] + SAFETY_MARGIN
        if free_bytes(db_path) < needed:
            raise PurgeRefused(f"{_gb(free_bytes(db_path))} libres, "
                               f"~{_gb(needed)} nécessaires à la reconstruction")
        before = p["db_size"]
        say("→ Reconstruction…")
        started = time.time()
        counts = rebuild(db_path, tmp_path, p["kept"], current)
        say("  vérifié : integrity_check ok, comptes identiques, aucun orphelin — "
            + ", ".join(f"{t}={n}" for t, n in counts.items()))
        wait_until_closed(db_path)
        swap(db_path, tmp_path)
        say(f"Terminé en {time.time() - started:.0f}s : {_gb(before)} -> "
            f"{_gb(os.path.getsize(db_path))}, {len(p['old'])} patch(s) purgé(s), "
            f"{_gb(free_bytes(db_path))} libres")
        rc = 0
    except PurgeRefused as exc:
        say(f"REFUS : {exc} — base d'origine intacte")
    except Exception as exc:  # noqa: BLE001 — on redémarre quoi qu'il arrive
        say(f"ÉCHEC : {type(exc).__name__}: {exc} — base d'origine intacte")
    finally:
        if rc != 0:
            remove_db_files(tmp_path)
        if was_running:
            say("→ Redémarrage du collecteur…")
            if _script("start.sh") != 0:
                say("! ÉCHEC DU REDÉMARRAGE — relancer start.sh à la main.")
    return rc
