#!/usr/bin/env python3
"""Instancie les gabarits récurrents dans la file éditoriale.

    python3 scripts/queue_sync.py [--patch 16.16] [--dry-run]

À chaque nouveau patch, les gabarits de queue/templates.json produisent leurs
articles dans queue/articles.json, avec leur patch cible et le statut
en_attente. Idempotent : relancer n'ajoute rien de nouveau et ne touche à
aucun statut existant — on peut donc l'appeler depuis le même cron que le
reste, sans condition.

Sans --patch, le patch courant vient de la base (le patch le plus récent
ayant des matchs), pas de Data Dragon : c'est le patch qu'on peut réellement
analyser, pas celui que Riot vient d'annoncer.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lolcollector.config import Config  # noqa: E402
from lolcollector.editorial import (  # noqa: E402
    TEMPLATES_PATH, DataState, load_json, load_queue, save_queue, sync_templates,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--patch", default=None,
                        help="patch à instancier ; défaut : patch courant en base")
    parser.add_argument("--dry-run", action="store_true",
                        help="liste ce qui serait créé, n'écrit rien")
    args = parser.parse_args()

    cfg = Config()
    patch = args.patch
    if patch is None:
        state = DataState(cfg.db_path)
        patch = state.patch
        if patch is None:
            print("Aucun patch en base : précise --patch X.Y.", file=sys.stderr)
            return 1
        print(f"Patch courant en base : {patch}")

    queue = load_queue()
    templates = load_json(TEMPLATES_PATH)["templates"]
    fresh = sync_templates(queue, templates, patch)

    if not fresh:
        print(f"Rien à faire : les gabarits sont déjà instanciés pour {patch}.")
        return 0

    for article in fresh:
        print(f"  + {article['id']:<40} {article['titre']}")
    if args.dry_run:
        print(f"\n{len(fresh)} article(s) seraient ajoutés (dry-run).")
        return 0

    save_queue(queue)
    print(f"\n{len(fresh)} article(s) ajoutés à la file pour le patch {patch}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
