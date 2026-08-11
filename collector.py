#!/usr/bin/env python3
"""lol-studies-collector — CLI.

  python collector.py run                                  lance les 3 workers
  python collector.py stats                                état du dataset
  python collector.py export --study tierlist --patch 16.14 [--out fichier.csv]
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

    export_parser = sub.add_parser(
        "export", help="export JSON d'une étude pour le site EloLab")
    export_parser.add_argument("--study", required=True, choices=["tierlist"],
                               help="étude à exporter")
    export_parser.add_argument("--patch", default=None,
                               help="patch ciblé (ex: 16.15) ; défaut : patch "
                                    "courant détecté via Data Dragon")
    export_parser.add_argument("--out", required=True,
                               help="répertoire de sortie (tierlist.json + meta.json)")
    export_parser.add_argument("--min-games", type=int, default=200,
                               help="sous ce nombre de games, une cellule est "
                                    "marquée insufficient_sample (défaut: 200)")

    args = parser.parse_args()
    cfg = Config()

    if args.command == "run":
        from lolcollector.worker import run_collector
        try:
            asyncio.run(run_collector())
        except KeyboardInterrupt:
            pass
    elif args.command == "stats":
        from lolcollector.stats import print_stats
        print_stats(cfg.db_path)
    elif args.command == "export":
        from lolcollector.export import export_tierlist
        if args.study == "tierlist":
            export_tierlist(cfg.db_path, args.patch, args.out,
                            min_games=args.min_games)


if __name__ == "__main__":
    sys.exit(main())
