"""Cycle complet de rafraîchissement d'une étude.

    python3 collector.py refresh --study tierlist

Arrêt du collecteur, export du patch courant vers sa destination déduite,
redémarrage, résumé.

Pourquoi arrêter le collecteur : l'export lit et surtout CRÉE des index sur
une base de plusieurs gigaoctets pendant que trois workers y écrivent. En
WAL, SQLite le supporte, mais la création d'index prend un verrou exclusif
et les workers se retrouvent en « database is locked » le temps qu'elle
dure.

Invariant : le collecteur est redémarré même si l'export échoue. Une étude
non rafraîchie est un contretemps ; un collecteur laissé à l'arrêt, c'est
de la donnée définitivement perdue — les matchs sortent de la fenêtre de
rétention de Riot et ne reviendront pas.
"""

import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _pid_file(cfg) -> str:
    path = cfg.pid_file
    return path if os.path.isabs(path) else os.path.join(REPO, path)


def collector_running(cfg) -> bool:
    """Vrai si le pid enregistré correspond à un processus vivant."""
    path = _pid_file(cfg)
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as fh:
            pid = int(fh.read().strip())
        os.kill(pid, 0)  # signal 0 : teste l'existence, n'envoie rien
    except (OSError, ValueError):
        return False
    return True


def _script(name: str) -> int:
    path = os.path.join(REPO, name)
    if not os.path.exists(path):
        print(f"! {name} introuvable dans {REPO}")
        return 1
    return subprocess.call(["bash", path], cwd=REPO)


def run_refresh(cfg, study: str, min_games: int = 200, force: bool = False) -> int:
    from .export import export_tierlist

    was_running = collector_running(cfg)
    if was_running:
        print("→ Arrêt du collecteur…")
        if _script("stop.sh") != 0:
            print("! stop.sh a échoué, export annulé (le collecteur tourne "
                  "toujours, rien n'est perdu).")
            return 1
    else:
        print("→ Collecteur déjà arrêté, il ne sera pas démarré par refresh.")

    export_error = None
    meta = None
    try:
        print(f"→ Export de l'étude {study} sur le patch courant…")
        meta = export_tierlist(cfg.db_path, None, None, min_games=min_games,
                               force=force, study=study)
    except SystemExit as exc:          # ExportRefused et SystemExit d'export
        export_error = str(exc)
    except Exception as exc:           # noqa: BLE001 — on redémarre quoi qu'il arrive
        export_error = f"{type(exc).__name__}: {exc}"
    finally:
        # Redémarrage inconditionnel : c'est tout l'intérêt du try/finally.
        if was_running:
            print("→ Redémarrage du collecteur…")
            if _script("start.sh") != 0:
                print("! ÉCHEC DU REDÉMARRAGE — relance start.sh à la main, "
                      "la collecte est à l'arrêt.")

    if export_error:
        print(f"\nRésumé : ÉCHEC.\n{export_error}")
        if was_running:
            print("Le collecteur, lui, a bien été redémarré.")
        return 1

    matches = f"{meta['total_matches']:,}".replace(",", " ")
    print(
        "\nRésumé"
        f"\n  patch             : {meta['patch']}"
        f"\n  matchs            : {matches}"
        f"\n  cellules valides  : {meta['usable_cells']} sur {meta['total_cells']}"
        f" (≥ {meta['min_cell_games']} games)"
        f"\n  cellules par rôle : {meta['role_cells']}"
        f"\n  destination       : {meta['out_dir']}"
    )
    return 0
