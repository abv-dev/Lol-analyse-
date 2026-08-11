"""Commande stats : volumes par région × bucket × patch, débit, taille de la base."""

import os
import sqlite3
import time


def _fmt_size(num_bytes: int) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} To"


def print_stats(db_path: str) -> None:
    if not os.path.exists(db_path):
        print(f"Base introuvable : {db_path} (le collecteur a-t-il déjà tourné ?)")
        return
    conn = sqlite3.connect(db_path)
    now = int(time.time())

    total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    last_hour = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE inserted_at > ?", (now - 3600,)
    ).fetchone()[0]
    last_24h = conn.execute(
        "SELECT COUNT(*) FROM matches WHERE inserted_at > ?", (now - 86400,)
    ).fetchone()[0]

    size = os.path.getsize(db_path)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            size += os.path.getsize(db_path + suffix)

    ddragon = conn.execute(
        "SELECT value FROM meta WHERE key = 'ddragon_current'"
    ).fetchone()

    print(f"Base           : {db_path} ({_fmt_size(size)})")
    print(f"Version ddragon: {ddragon[0] if ddragon else 'inconnue'}")
    print(f"Matchs totaux  : {total}")
    print(f"Débit          : {last_hour} matchs/h (dernière heure), "
          f"{last_24h} sur 24h (~{last_24h / 24:.0f}/h)")

    # Couverture timeline du patch courant. Les comptes passent par l'index
    # idx_tl_state_status et par (patch, …) : pas de scan des tables de
    # timeline, qui sont les plus volumineuses de la base.
    if ddragon:
        current_patch = ".".join(ddragon[0].split(".")[:2])
        patch_matches = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE patch = ?", (current_patch,)
        ).fetchone()[0]
        stored, skipped, missing = 0, 0, 0
        rows = conn.execute(
            "SELECT t.status, COUNT(*) FROM timeline_state t"
            " JOIN matches m ON m.match_id = t.match_id"
            " WHERE m.patch = ? GROUP BY t.status", (current_patch,)
        ).fetchall()
        for status, count in rows:
            if status == "ok":
                stored = count
            elif status == "skipped":
                skipped = count
            elif status == "missing":
                missing = count
        decided = stored + skipped + missing
        pending = max(0, patch_matches - decided)
        if patch_matches:
            print(f"Timelines {current_patch}: {stored} stockées "
                  f"({stored / patch_matches * 100:.1f} % des matchs du patch) ; "
                  f"{skipped} hors échantillon, {missing} absentes chez Riot, "
                  f"{pending} en attente")
    print()

    rows = conn.execute(
        "SELECT region, tier_bucket_source, patch, COUNT(*)"
        " FROM matches GROUP BY region, tier_bucket_source, patch"
        " ORDER BY region, patch DESC, tier_bucket_source"
    ).fetchall()
    if not rows:
        print("Aucun match en base pour l'instant.")
        conn.close()
        return

    header = f"{'région':<10} {'bucket':<14} {'patch':<7} {'matchs':>7}"
    print(header)
    print("-" * len(header))
    for region, bucket, patch, count in rows:
        print(f"{region:<10} {bucket or '?':<14} {patch or '?':<7} {count:>7}")
    conn.close()
