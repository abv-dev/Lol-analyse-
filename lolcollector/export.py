"""Commande export : agrégats CSV pour études (tierlist winrate/pick/ban)."""

import csv
import os
import sqlite3
import sys


def export_tierlist(db_path: str, patch: str, out_path: str | None) -> None:
    """CSV winrate/pickrate/banrate par champion × bucket × région pour un patch."""
    if not os.path.exists(db_path):
        raise SystemExit(f"Base introuvable : {db_path}")
    conn = sqlite3.connect(db_path)

    # Nombre de matchs par (région, bucket) pour ce patch — dénominateur des taux
    group_sizes = {
        (region, bucket): count
        for region, bucket, count in conn.execute(
            "SELECT region, tier_bucket_source, COUNT(*) FROM matches"
            " WHERE patch = ? GROUP BY region, tier_bucket_source", (patch,)
        )
    }
    if not group_sizes:
        raise SystemExit(f"Aucun match en base pour le patch {patch}")

    # Picks et wins
    picks = {}
    for champ_id, champ_name, region, bucket, pick_count, wins in conn.execute(
        "SELECT p.champion_id, p.champion_name, m.region, m.tier_bucket_source,"
        " COUNT(*), SUM(p.win)"
        " FROM participants p JOIN matches m ON m.match_id = p.match_id"
        " WHERE m.patch = ?"
        " GROUP BY p.champion_id, m.region, m.tier_bucket_source", (patch,)
    ):
        picks[(champ_id, region, bucket)] = (champ_name, pick_count, wins or 0)

    # Bans (champion_id = -1 quand une équipe ne ban pas : exclu)
    bans = {
        (champ_id, region, bucket): ban_count
        for champ_id, region, bucket, ban_count in conn.execute(
            "SELECT b.champion_id, m.region, m.tier_bucket_source, COUNT(*)"
            " FROM bans b JOIN matches m ON m.match_id = b.match_id"
            " WHERE m.patch = ? AND b.champion_id > 0"
            " GROUP BY b.champion_id, m.region, m.tier_bucket_source", (patch,)
        )
    }

    # Noms de champions (pour les bans jamais joués dans le groupe)
    names = dict(conn.execute(
        "SELECT DISTINCT champion_id, champion_name FROM participants"
        " WHERE champion_name IS NOT NULL"
    ))
    conn.close()

    all_keys = sorted(set(picks) | set(bans),
                      key=lambda k: (k[1], k[2], names.get(k[0], ""), k[0]))

    out = open(out_path, "w", newline="", encoding="utf-8") if out_path else sys.stdout
    try:
        writer = csv.writer(out)
        writer.writerow([
            "patch", "region", "tier_bucket", "champion_id", "champion_name",
            "matches_in_group", "picks", "wins", "winrate",
            "pickrate", "bans", "banrate",
        ])
        for champ_id, region, bucket in all_keys:
            group_matches = group_sizes.get((region, bucket), 0)
            champ_name, pick_count, wins = picks.get(
                (champ_id, region, bucket), (names.get(champ_id, ""), 0, 0)
            )
            ban_count = bans.get((champ_id, region, bucket), 0)
            winrate = wins / pick_count if pick_count else ""
            pickrate = pick_count / group_matches if group_matches else ""
            banrate = ban_count / group_matches if group_matches else ""
            writer.writerow([
                patch, region, bucket, champ_id, champ_name or names.get(champ_id, ""),
                group_matches, pick_count, wins,
                f"{winrate:.4f}" if winrate != "" else "",
                f"{pickrate:.4f}" if pickrate != "" else "",
                ban_count,
                f"{banrate:.4f}" if banrate != "" else "",
            ])
    finally:
        if out_path:
            out.close()
            print(f"Export écrit : {out_path} ({len(all_keys)} lignes)")
