"""Backfill de `participants.participant_id` — étape encadrée du Lot 14.

    ./stop.sh
    python3 collector.py backfill-participant-ids
    ./start.sh

C'est la seule écriture autorisée sur la base de production hors collecte, et
elle se fait collecteur ARRÊTÉ : l'UPDATE porte sur ~22 M de lignes et prend
un verrou d'écriture le temps de la transaction. La commande refuse donc de
tourner si le pid du collecteur répond encore.

Le garde-fou (chaque match à 10 participants porte exactement les ids 1-10)
tourne dans la même transaction que l'UPDATE : s'il déclenche, rien n'est
écrit et la commande sort en code non nul.
"""

import time

from .db import (
    Database,
    ParticipantIdError,
    backfill_participant_ids,
    check_participant_ids,
)
from .refresh import collector_running


def run_participant_id_backfill(cfg, force: bool = False) -> int:
    if collector_running(cfg) and not force:
        print("! Le collecteur tourne : arrête-le d'abord (./stop.sh), sinon "
              "l'UPDATE verrouille la base en écriture pendant que les workers "
              "insèrent.")
        return 1

    db = Database(cfg.db_path)
    try:
        avant = db.conn.execute(
            "SELECT COUNT(*) FROM participants WHERE participant_id IS NULL"
        ).fetchone()[0]
        print(f"→ {avant:,} lignes sans participant_id".replace(",", " "))
        if avant == 0:
            total, exemples = check_participant_ids(db.conn)
            print("Rien à backfiller." if not total else
                  f"! {total} match(s) incohérents en base (ex. : "
                  f"{', '.join(exemples)})")
            return 1 if total else 0

        started = time.time()
        try:
            modifiees = backfill_participant_ids(db.conn)
        except ParticipantIdError as exc:
            print(f"! Garde-fou déclenché, backfill annulé : {exc}")
            return 1
        print(f"OK  {modifiees} lignes renseignées en {time.time() - started:.1f} s, "
              "garde-fou passé (chaque match à 10 participants porte les "
              "participant_id 1-10).")
        return 0
    finally:
        db.close()
