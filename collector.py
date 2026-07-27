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

    export_parser = sub.add_parser("export", help="export CSV agrégé pour une étude")
    export_parser.add_argument("--study", required=True, choices=["tierlist"],
                               help="étude à exporter")
    export_parser.add_argument("--patch", required=True,
                               help="patch ciblé, ex: 16.14")
    export_parser.add_argument("--out", default=None,
                               help="fichier CSV de sortie (défaut: stdout)")

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
            export_tierlist(cfg.db_path, args.patch, args.out)


if __name__ == "__main__":
    sys.exit(main())
