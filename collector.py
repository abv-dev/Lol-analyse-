#!/usr/bin/env python3
"""lol-studies-collector — CLI.

  python collector.py run                     lance les 3 workers
  python collector.py stats                   état du dataset
  python collector.py refresh --study tierlist   arrêt, export, redémarrage
  python collector.py export --study tierlist [--patch 16.14] [--out dir]
"""

import argparse
import asyncio
import sys

from lolcollector.config import Config


def main() -> None:
    parser = argparse.ArgumentParser(prog="collector.py",
                                     description="Collecteur de matchs ranked solo Riot")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="lance les 3 workers régionaux (europe/asia/americas)")
    sub.add_parser("stats", help="matchs par région × bucket × patch, débit, taille db")

    backfill_parser = sub.add_parser(
        "backfill-timelines",
        help="récupère les timelines de matchs déjà en base (patch courant d'abord)")
    backfill_parser.add_argument("--limit", type=int, default=1000,
                                 help="nombre max de timelines à récupérer (défaut: 1000)")
    backfill_parser.add_argument("--share", type=float, default=0.3,
                                 help="part du budget de requêtes régional allouée "
                                      "au backfill, pour ne pas affamer les workers "
                                      "(défaut: 0.3)")

    prune_parser = sub.add_parser(
        "prune",
        help="supprime les matchs bruts (et timelines) des vieux patchs déjà exportés")
    prune_parser.add_argument("--keep-patches", type=int, default=2,
                              help="nombre de patchs récents à conserver (défaut: 2)")
    prune_parser.add_argument("--exports", default=None,
                              help="répertoire des études exportées "
                                   "(défaut: site/data/etudes)")
    prune_parser.add_argument("--yes", action="store_true",
                              help="ne pas demander de confirmation")

    export_parser = sub.add_parser(
        "export", help="export JSON d'une étude pour le site EloLab")
    export_parser.add_argument("--study", required=True, choices=["tierlist"],
                               help="étude à exporter")
    export_parser.add_argument("--patch", default=None,
                               help="patch ciblé (ex: 16.15) ; défaut : patch "
                                    "courant détecté via Data Dragon")
    export_parser.add_argument("--out", default=None,
                               help="répertoire de sortie ; par défaut "
                                    "site/data/etudes/<étude>/<patch-slug>/, "
                                    "déduit du patch exporté")
    export_parser.add_argument("--min-games", type=int, default=200,
                               help="sous ce nombre de games, une cellule est "
                                    "marquée insufficient_sample (défaut: 200)")
    export_parser.add_argument("--force", action="store_true",
                               help="écrire même si aucune cellule n'atteint "
                                    "--min-games (ne contourne pas le contrôle "
                                    "de patch de la destination)")

    refresh_parser = sub.add_parser(
        "refresh",
        help="cycle complet : arrêt du collecteur, export du patch courant, "
             "redémarrage, résumé")
    refresh_parser.add_argument("--study", default="tierlist", choices=["tierlist"],
                                help="étude à rafraîchir (défaut: tierlist)")
    refresh_parser.add_argument("--min-games", type=int, default=200,
                                help="seuil de cellule exploitable (défaut: 200)")
    refresh_parser.add_argument("--force", action="store_true",
                                help="exporter même sans cellule au-dessus du seuil")

    args = parser.parse_args()
    cfg = Config()

    if args.command == "run":
        from lolcollector.worker import run_collector
        try:
            asyncio.run(run_collector())
        except KeyboardInterrupt:
            pass
    elif args.command == "backfill-timelines":
        from lolcollector.backfill import run_backfill
        try:
            return asyncio.run(run_backfill(args.limit, args.share))
        except KeyboardInterrupt:
            pass
    elif args.command == "prune":
        from lolcollector.prune import DEFAULT_EXPORTS_DIR, run_prune
        return run_prune(cfg.db_path, args.keep_patches,
                         args.exports or DEFAULT_EXPORTS_DIR, args.yes)
    elif args.command == "stats":
        from lolcollector.stats import print_stats
        print_stats(cfg.db_path)
    elif args.command == "export":
        from lolcollector.export import export_tierlist
        if args.study == "tierlist":
            export_tierlist(cfg.db_path, args.patch, args.out,
                            min_games=args.min_games, force=args.force,
                            study=args.study)
    elif args.command == "refresh":
        from lolcollector.refresh import run_refresh
        return run_refresh(cfg, args.study, min_games=args.min_games,
                           force=args.force)


if __name__ == "__main__":
    sys.exit(main())
