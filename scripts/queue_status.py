#!/usr/bin/env python3
"""État de la file éditoriale : quoi est publiable maintenant, et qui attend quoi.

    python3 scripts/queue_status.py [--stock-min 15] [--json]

Trois sections :

1. **Données** — patch courant, son âge, son volume, et ce qui est
   disponible en base.
2. **Rédigeables maintenant** — dans l'ordre où le publieur les prendrait.
   L'ordre n'est pas figé : quand le stock est bas, le structurel passe
   devant, parce qu'il ne périme pas et reste disponible les jours où un
   patch vient de sortir.
3. **En attente** — chaque article bloqué, avec ce qui lui manque
   exactement.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lolcollector.config import Config  # noqa: E402
from lolcollector.editorial import (  # noqa: E402
    MIN_PATCH_AGE_DAYS, MIN_PATCH_MATCHES, DataState, blockers, load_queue,
    selection_order, stock_count,
)

DEFAULT_STOCK_MIN = 15
REGIME_LABEL = {"patch_courant": "patch", "structurel": "struct.",
                "comparatif": "compar."}


def num(value: int) -> str:
    """Séparateur de milliers à la française, sans toucher au reste de la
    phrase — un .replace(',', ' ') sur la ligne entière mangeait les virgules
    de la prose."""
    return f"{value:,}".replace(",", " ")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--stock-min", type=int, default=DEFAULT_STOCK_MIN,
                        help=f"seuil sous lequel le structurel est priorisé "
                             f"(défaut: {DEFAULT_STOCK_MIN})")
    parser.add_argument("--json", action="store_true",
                        help="sortie machine, pour batch_write.py")
    args = parser.parse_args()

    cfg = Config()
    state = DataState(cfg.db_path)
    queue = load_queue()
    articles = queue.get("articles", [])
    stock = stock_count(articles)
    ordered = selection_order(articles, state, stock, args.stock_min)
    ready_ids = {a["id"] for a in ordered}

    if args.json:
        print(json.dumps({
            "patch": state.patch,
            "patch_age_days": state.patch_age_days,
            "patch_mature": state.patch_mature,
            "total_matches": state.total_matches,
            "previous_patch": state.previous_patch,
            "stock": stock,
            "stock_min": args.stock_min,
            "redigeables": [a["id"] for a in ordered],
            "bloques": {a["id"]: blockers(a, state)
                        for a in articles if a["id"] not in ready_ids},
        }, ensure_ascii=False, indent=1))
        return 0

    # ---- 1. données
    print("DONNÉES")
    if not state.available:
        print(f"  base introuvable : {cfg.db_path}")
    elif state.patch is None:
        print("  aucun match en base")
    else:
        age = ("inconnu" if state.patch_age_days is None
               else f"{state.patch_age_days:.1f} jours")
        verdict = "mûr" if state.patch_mature else "trop jeune ou trop maigre"
        print(f"  patch courant   : {state.patch} ({age}) — {verdict}")
        print(f"  matchs          : {num(state.total_matches)}"
              f"  (seuil {num(MIN_PATCH_MATCHES)})")
        if state.previous_patch:
            print(f"  patch précédent : {state.previous_patch} "
                  f"({num(state.previous_matches)} matchs)")
        if not state.patch_mature:
            print(f"  → les études « patch courant » attendent "
                  f"{MIN_PATCH_AGE_DAYS:g} jours et {num(MIN_PATCH_MATCHES)} "
                  f"matchs ; le comparatif et le structurel, eux, "
                  f"restent ouverts.")

    # ---- 2. stock
    print(f"\nSTOCK : {stock} article(s) vérifié(s) en attente de publication "
          f"(cible ≥ {args.stock_min})")
    if stock < args.stock_min:
        print("  → stock bas : le structurel est priorisé, c'est le tampon.")

    # ---- 3. rédigeables
    print(f"\nRÉDIGEABLES MAINTENANT ({len(ordered)}) — dans l'ordre de sélection")
    if not ordered:
        print("  aucun")
    for i, article in enumerate(ordered, 1):
        regime = REGIME_LABEL.get(article.get("regime"), "?")
        print(f"  {i:>2}. [{regime:<7}] {article['id']:<38} {article.get('titre','')}")

    # ---- 4. en attente
    waiting = [a for a in articles if a["id"] not in ready_ids]
    print(f"\nEN ATTENTE ({len(waiting)})")
    if not waiting:
        print("  aucun")
    for article in waiting:
        reasons = blockers(article, state)
        regime = REGIME_LABEL.get(article.get("regime"), "?")
        print(f"  [{regime:<7}] {article['id']}")
        for reason in reasons:
            print(f"            ↳ {reason}")

    if not articles:
        print("\nLa file est vide. Instancie les gabarits récurrents avec "
              "scripts/queue_sync.py, et ajoute les sujets structurels dans "
              "queue/articles.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
