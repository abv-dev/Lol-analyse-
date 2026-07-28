#!/usr/bin/env python3
"""Jalons de collecte : vérifie le volume et crée des tâches Todoist.

Pensé pour tourner en cron. Idempotent, silencieux quand rien à signaler,
logs dans logs/milestones.log. Aucune dépendance hors stdlib (urllib).

- Compte les matchs du patch courant (version ddragon vue par le collecteur)
  et le total toutes régions dans data/matches.db.
- Seuils MILESTONES (env, ex "50000,100000") : à chaque franchissement sur le
  patch courant, crée une tâche p2 dans le projet Todoist "LoL Studies"
  (résolu par NOM via l'API). Un seuil n'est notifié qu'une fois par patch ;
  sur nouveau patch les seuils redeviennent notifiables.
- Panne : si aucun match inséré depuis OUTAGE_AFTER_HOURS (3h), crée une
  tâche p1, une seule fois tant que la panne dure.
- État persisté dans data/milestones_done.json :
  {"milestones": {"16.14": [50000]}, "outage_notified": false}
"""

import json
import logging
import logging.handlers
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lolcollector.config import Config  # noqa: E402
from lolcollector.db import patch_of  # noqa: E402

TODOIST_API = "https://api.todoist.com/rest/v2"
DEFAULT_MILESTONES = "50000,100000,250000,500000"
DEFAULT_PROJECT_NAME = "LoL Studies"
OUTAGE_AFTER_HOURS = 3

# Étape d'étude débloquée par chaque palier (description de la tâche)
STUDY_STAGES = {
    50_000: "première tierlist globale par bucket exploitable",
    100_000: "winrates fiables par rôle et par bucket",
    250_000: "comparaisons par région et champions à pickrate moyen",
    500_000: "méta complète, y compris champions à faible pickrate",
}


def setup_logging(log_dir: str) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("milestones")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "milestones.log"),
            maxBytes=1024 * 1024, backupCount=3, encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return logger


# ---------------------------------------------------------------------------
# État persisté
# ---------------------------------------------------------------------------

def state_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(db_path) or ".", "milestones_done.json")


def load_state(path: str) -> dict:
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    else:
        state = {}
    state.setdefault("milestones", {})
    state.setdefault("outage_notified", False)
    return state


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Todoist (API REST v2, urllib uniquement)
# ---------------------------------------------------------------------------

def todoist_request(token: str, method: str, endpoint: str, payload: dict | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{TODOIST_API}{endpoint}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def resolve_project_id(token: str, name: str) -> str | None:
    """Résout l'id du projet par son nom (jamais d'id en dur)."""
    projects = todoist_request(token, "GET", "/projects") or []
    wanted = name.strip().casefold()
    for project in projects:
        if project.get("name", "").strip().casefold() == wanted:
            return project["id"]
    return None


def create_task(token: str, project_id: str, content: str, description: str,
                priority_p: int) -> None:
    """Crée une tâche échue aujourd'hui. priority_p est au sens Todoist UI
    (p1 = plus haute) ; l'API REST inverse l'échelle : p1 -> 4, p2 -> 3."""
    todoist_request(token, "POST", "/tasks", {
        "content": content,
        "description": description,
        "project_id": project_id,
        "priority": 5 - priority_p,
        "due_string": "today",
    })


# ---------------------------------------------------------------------------
# Lectures base
# ---------------------------------------------------------------------------

def region_bucket_breakdown(conn: sqlite3.Connection, patch: str) -> str:
    rows = conn.execute(
        "SELECT region, tier_bucket_source, COUNT(*) FROM matches"
        " WHERE patch = ? GROUP BY region, tier_bucket_source"
        " ORDER BY region, tier_bucket_source", (patch,)
    ).fetchall()
    return "\n".join(f"- {region} / {bucket or '?'} : {count}" for region, bucket, count in rows)


def main() -> int:
    cfg = Config()
    log = setup_logging(cfg.log_dir)

    if not os.path.exists(cfg.db_path):
        log.info("base %s absente, rien à faire", cfg.db_path)
        return 0

    conn = sqlite3.connect(cfg.db_path)
    try:
        ddragon = conn.execute(
            "SELECT value FROM meta WHERE key = 'ddragon_current'"
        ).fetchone()
        if not ddragon:
            log.info("version ddragon inconnue (collecteur jamais lancé ?), rien à faire")
            return 0
        patch = patch_of(ddragon[0])

        total = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        patch_count = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE patch = ?", (patch,)
        ).fetchone()[0]
        last_insert = conn.execute("SELECT MAX(inserted_at) FROM matches").fetchone()[0]

        milestones = sorted(
            int(m) for m in os.environ.get("MILESTONES", DEFAULT_MILESTONES).split(",") if m.strip()
        )
        spath = state_path(cfg.db_path)
        state = load_state(spath)
        done_for_patch: list = state["milestones"].setdefault(patch, [])

        crossed = [m for m in milestones if patch_count >= m and m not in done_for_patch]

        now = int(time.time())
        outage = last_insert is not None and now - last_insert > OUTAGE_AFTER_HOURS * 3600
        outage_to_notify = outage and not state["outage_notified"]
        if not outage and state["outage_notified"]:
            # La collecte a repris : réarme l'alerte pour la prochaine panne
            state["outage_notified"] = False
            save_state(spath, state)
            log.info("collecte reprise, alerte panne réarmée")

        if not crossed and not outage_to_notify:
            log.info("patch %s : %d matchs (total %d), rien à signaler",
                     patch, patch_count, total)
            return 0

        token = os.environ.get("TODOIST_API_TOKEN", "")
        if not token:
            log.error("TODOIST_API_TOKEN manquant alors qu'il y a à notifier "
                      "(seuils %s, panne=%s)", crossed, outage_to_notify)
            return 1

        project_name = os.environ.get("TODOIST_PROJECT_NAME", DEFAULT_PROJECT_NAME)
        try:
            project_id = resolve_project_id(token, project_name)
        except (urllib.error.URLError, urllib.error.HTTPError) as exc:
            log.error("Todoist injoignable (%s), on retentera au prochain run", exc)
            return 1
        if not project_id:
            log.error("projet Todoist %r introuvable", project_name)
            return 1

        # Jalons de volume — un seuil n'est marqué fait QU'APRÈS création réussie
        for milestone in crossed:
            stage = STUDY_STAGES.get(
                milestone, "étape d'étude suivante prête à être lancée")
            description = (
                f"Répartition région × bucket au franchissement "
                f"({patch_count} matchs sur le patch {patch}, {total} au total) :\n"
                f"{region_bucket_breakdown(conn, patch)}\n\n"
                f"Le dataset est prêt pour l'étape d'étude correspondante : {stage}."
            )
            try:
                create_task(token, project_id,
                            f"EloLab : {milestone} matchs collectés en {patch}",
                            description, priority_p=2)
            except (urllib.error.URLError, urllib.error.HTTPError) as exc:
                log.error("création de tâche échouée pour le seuil %d (%s), "
                          "on retentera au prochain run", milestone, exc)
                return 1
            done_for_patch.append(milestone)
            save_state(spath, state)
            log.info("seuil %d notifié pour le patch %s", milestone, patch)

        # Alerte panne — une seule fois tant que la panne dure
        if outage_to_notify:
            silent_hours = (now - last_insert) / 3600
            description = (
                f"Aucun match inséré depuis {silent_hours:.1f} h "
                f"(dernier insert : {time.strftime('%Y-%m-%d %H:%M', time.localtime(last_insert))}).\n"
                "À vérifier sur le serveur : clé API expirée (401/403 dans "
                "logs/collector.log) ? processus mort (collector.pid) ? "
                "Relancer avec ./start.sh après correction."
            )
            try:
                create_task(token, project_id,
                            "EloLab : le collecteur semble arrêté",
                            description, priority_p=1)
            except (urllib.error.URLError, urllib.error.HTTPError) as exc:
                log.error("création de l'alerte panne échouée (%s)", exc)
                return 1
            state["outage_notified"] = True
            save_state(spath, state)
            log.warning("alerte panne notifiée (silence de %.1f h)", silent_hours)

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
