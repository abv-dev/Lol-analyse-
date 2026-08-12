#!/usr/bin/env python3
"""
EloLab — orchestrateur multi-modèles.

Principe : AUCUN LLM dans l'orchestrateur. Il route, lance, compte, journalise.
Les décisions viennent du CTO (Fable), le code/texte du dev (Opus), et le verdict
final des vérificateurs déterministes (pytest / verify_study.py).

Deux configurations, un seul moteur :
  - config "code"      : lots du module Coach
  - config "redaction" : articles de la file éditoriale

Usage :
  python orchestrator.py --config code --plan docs/coach-plan.md --dry-run
  python orchestrator.py --config code --plan docs/coach-plan.md --lot 1
  python orchestrator.py --config redaction --queue queue/articles.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

REPO = Path(os.environ.get("ELOLAB_REPO", Path.cwd()))
STATE_DIR = REPO / ".orchestrator"
LOG_FILE = STATE_DIR / "run.log"

MODEL_CTO = os.environ.get("MODEL_CTO", "claude-fable-5")
MODEL_DEV = os.environ.get("MODEL_DEV", "claude-opus-5")

MAX_ATTEMPTS = 3
CLAUDE_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "3600"))  # 1 h par appel

TODOIST_PROJECT_ID = "6h7wgfx38fWgC6c5"  # LoL Studies


# --------------------------------------------------------------------------
# Journalisation
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    print(line, flush=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


# --------------------------------------------------------------------------
# Appel d'un agent Claude Code (processus isolé = contexte isolé)
# --------------------------------------------------------------------------

def call_agent(role: str, model: str, prompt: str, dry_run: bool = False) -> str:
    """
    Lance `claude -p` dans un sous-processus dédié.

    IMPORTANT : chaque appel est un processus neuf. C'est ce qui garantit que
    le relecteur ne voit pas le raisonnement du rédacteur — seulement son
    livrable. Ne jamais fusionner ces appels dans une session partagée.
    """
    log(f"→ {role} ({model}) — {len(prompt)} caractères de prompt")

    if dry_run:
        log(f"   [dry-run] appel non exécuté")
        return f"[DRY-RUN {role}]"

    cmd = [
        "claude",
        "-p", prompt,
        "--model", model,
        "--permission-mode", "acceptEdits",
    ]

    try:
        result = subprocess.run(
            cmd,
            cwd=REPO,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log(f"   ✗ {role} : timeout après {CLAUDE_TIMEOUT}s")
        return ""
    except FileNotFoundError:
        log("   ✗ binaire `claude` introuvable — Claude Code est-il installé ?")
        sys.exit(1)

    if result.returncode != 0:
        log(f"   ✗ {role} : code {result.returncode}")
        log(f"     stderr : {result.stderr[:800]}")
        return ""

    log(f"   ✓ {role} : {len(result.stdout)} caractères de réponse")
    return result.stdout


# --------------------------------------------------------------------------
# Vérificateurs déterministes — LE juge d'appel
# --------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    output: str

    def summary(self) -> str:
        mark = "✓" if self.passed else "✗"
        return f"{mark} {self.name}"


def run_check(name: str, cmd: list[str], dry_run: bool = False) -> CheckResult:
    if dry_run:
        return CheckResult(name, True, "[dry-run]")
    try:
        r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=1800)
    except Exception as e:  # noqa: BLE001
        return CheckResult(name, False, f"exception : {e}")
    out = (r.stdout + "\n" + r.stderr).strip()
    return CheckResult(name, r.returncode == 0, out[-4000:])


CHECKS = {
    "code": [
        ("pytest", ["python", "-m", "pytest", "-q"]),
        ("ruff", ["python", "-m", "ruff", "check", "."]),
    ],
    "redaction": [
        ("verify_study", ["python", "scripts/verify_study.py", "--all"]),
    ],
}


def run_all_checks(config: str, dry_run: bool = False) -> list[CheckResult]:
    results = []
    for name, cmd in CHECKS[config]:
        res = run_check(name, cmd, dry_run)
        log(f"   {res.summary()}")
        results.append(res)
    return results


# --------------------------------------------------------------------------
# Todoist (échec bloquant)
# --------------------------------------------------------------------------

def todoist_task(title: str, body: str) -> None:
    token = os.environ.get("TODOIST_API_TOKEN")
    if not token:
        log("   ! TODOIST_API_TOKEN absent — tâche non créée")
        return
    import urllib.request

    payload = json.dumps({
        "content": title,
        "description": body[:4000],
        "project_id": TODOIST_PROJECT_ID,
        "priority": 3,
    }).encode()

    req = urllib.request.Request(
        "https://api.todoist.com/api/v1/tasks",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            log(f"   ✓ tâche Todoist créée (HTTP {r.status})")
    except Exception as e:  # noqa: BLE001
        log(f"   ! Todoist a échoué : {e}")


# --------------------------------------------------------------------------
# État persistant (reprise après coupure)
# --------------------------------------------------------------------------

def load_state() -> dict:
    p = STATE_DIR / "state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {"items": {}}


def save_state(state: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "state.json").write_text(
        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Parsing du plan
# --------------------------------------------------------------------------

@dataclass
class Item:
    id: str
    title: str
    body: str
    depends_on: list[str]
    state: str


def parse_plan(path: Path) -> list[Item]:
    """
    Lit docs/coach-plan.md. Format attendu par lot :

    ## Lot 3 — Titre du lot
    Objectif : ...
    Critère : ...
    Dépend de : Lot 1, Lot 2
    État : à faire
    """
    text = path.read_text(encoding="utf-8")
    blocks = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    items = []
    for b in blocks:
        first_line = b.splitlines()[0].strip()
        m = re.match(r"(Lot\s+\d+)\s*[—-]\s*(.+)", first_line)
        if not m:
            continue
        lot_id, title = m.group(1).strip(), m.group(2).strip()
        deps_m = re.search(r"^D[ée]pend de\s*:\s*(.+)$", b, flags=re.MULTILINE)
        deps = []
        if deps_m and deps_m.group(1).strip() not in {"—", "-", "aucun"}:
            deps = [d.strip() for d in deps_m.group(1).split(",")]
        state_m = re.search(r"^[ÉE]tat\s*:\s*(.+)$", b, flags=re.MULTILINE)
        state = state_m.group(1).strip() if state_m else "à faire"
        items.append(Item(lot_id, title, b.strip(), deps, state))
    return items


def load_queue(path: Path) -> list[Item]:
    data = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for a in data.get("articles", []):
        if a.get("statut") != "a_rediger":
            continue
        items.append(Item(
            id=a["id"],
            title=a.get("titre", a["id"]),
            body=json.dumps(a, indent=2, ensure_ascii=False),
            depends_on=[],
            state="à faire",
        ))
    return items


# --------------------------------------------------------------------------
# Prompts des rôles
# --------------------------------------------------------------------------

CTO_SPEC = """Tu es le CTO technique du projet EloLab. Tu ne codes JAMAIS — tu spécifies.

Lis d'abord dans le repo : docs/editorial.md, docs/coach-plan.md, le schéma réel
de la base, et le résultat de out/coverage/ si présent.

Voici le lot à spécifier :

{body}

Produis dans specs/{item_id}.md une spécification technique contenant :
1. Le périmètre exact — et ce qui est HORS périmètre
2. Les fichiers à créer ou modifier, un par un
3. Les signatures et structures de données précises
4. Les cas limites et le comportement de dégradation
5. Les tests à écrire, avec leurs assertions exactes
6. Les critères de succès automatiques et vérifiables

Contraintes non négociables :
- Aucune requête sans index sur `participants` (22 M de lignes)
- Aucun nom de colonne deviné : lis le schéma
- Si le lot dépend d'une donnée absente de la base, écris-le et ARRÊTE-TOI

N'écris que le fichier de spec. Ne code pas. Ne modifie aucun autre fichier."""


DEV_IMPL = """Tu es développeur sur le projet EloLab. Implémente exactement la
spécification ci-dessous, rien de plus.

Lis specs/{item_id}.md et applique-la.

Contraintes :
- Branche : {branch}
- Écris les tests spécifiés AVANT le code
- Aucun refactor hors périmètre, aucune dépendance nouvelle non listée
- Le collecteur tourne : aucune écriture dans la base de production
- Si la spec est ambiguë ou impossible, écris pourquoi dans
  specs/{item_id}.blocked.md et arrête-toi. Ne devine pas.

Termine par un commit sur la branche."""


DEV_FIX = """Ta tentative précédente a échoué aux vérifications. Voici les logs :

{logs}

Corrige. Ne change pas le périmètre. Ne désactive ni ne contourne aucun test."""


CTO_REVIEW = """Tu es le CTO du projet EloLab, en relecture de code.

Tu n'as PAS écrit ce code et tu n'as pas vu le raisonnement de son auteur.
C'est volontaire : juge le livrable, pas l'intention.

Lis specs/{item_id}.md, puis le diff de la branche {branch} (`git diff main...HEAD`).

Vérifie précisément :
- La spec est-elle respectée intégralement ?
- Une requête peut-elle scanner `participants` sans index ?
- Un seuil ou une hypothèse est-il codé en dur sans justification mesurée ?
- Un test a-t-il été affaibli, désactivé ou rendu tautologique ?
- Pour la rédaction : la charte causalité/traçabilité de docs/editorial.md est-elle
  respectée ? Une affirmation est-elle non traçable jusqu'à un JSON chargé ?

Réponds en commençant OBLIGATOIREMENT par une seule ligne :
VERDICT: APPROUVE
ou
VERDICT: REJETE

Puis, si rejeté, la liste numérotée des problèmes avec fichier et ligne.
Pas de nuance, pas de « globalement bon mais ». Binaire."""


REDACTION_IMPL = """Tu rédiges un article pour EloLab.

Lis IMPÉRATIVEMENT avant d'écrire :
- docs/editorial.md (la charte — contraignante)
- .claude/skills/elolab-redaction/SKILL.md
- site/content/etudes/tierlist/16-15/index.mdx (l'article de référence, ton modèle
  de ton et de structure)

Article à rédiger :

{body}

Règles absolues :
- Chaque nombre doit être lisible dans le JSON source. Aucun chiffre inventé,
  aucun arrondi non vérifiable.
- Chaque nombre accompagné de son effectif et de son IC Wilson 95 %.
- AUCUN lien de cause à effet entre deux statistiques. « car », « grâce à »,
  « parce que » reliant deux stats sont interdits.
- AUCUNE explication mécanique (scaling, chemins de build) sauf si elle est
  traçable jusqu'à une source de données CHARGÉE dans le pipeline.
- Le fichier doit passer `python scripts/verify_study.py`.

Écris index.mdx et meta.json dans le dossier de l'étude. Rien d'autre."""


# --------------------------------------------------------------------------
# Boucle principale
# --------------------------------------------------------------------------

def process_item(item: Item, config: str, state: dict, dry_run: bool) -> str:
    key = item.id
    entry = state["items"].setdefault(key, {"attempts": 0, "state": "à faire"})

    if entry["state"] in {"termine", "bloque"}:
        log(f"— {item.id} déjà en état « {entry['state']} », ignoré")
        return entry["state"]

    branch = f"orchestrator/{item.id.lower().replace(' ', '-')}"
    log(f"\n=== {item.id} — {item.title} ===")

    # Étape 1 : spécification (code uniquement ; la rédaction a déjà sa file)
    if config == "code":
        spec_path = REPO / "specs" / f"{item.id}.md"
        if not spec_path.exists():
            call_agent(
                "CTO/spec", MODEL_CTO,
                CTO_SPEC.format(body=item.body, item_id=item.id),
                dry_run,
            )
        blocked = REPO / "specs" / f"{item.id}.blocked.md"
        if blocked.exists():
            log("   ✗ le CTO a déclaré le lot bloqué")
            todoist_task(
                f"[Orchestrateur] {item.id} bloqué à la spécification",
                blocked.read_text(encoding="utf-8")[:3000],
            )
            entry["state"] = "bloque"
            return "bloque"

    if not dry_run:
        subprocess.run(["git", "checkout", "-B", branch], cwd=REPO,
                       capture_output=True, text=True)

    logs = ""
    while entry["attempts"] < MAX_ATTEMPTS:
        entry["attempts"] += 1
        save_state(state)
        log(f"-- tentative {entry['attempts']}/{MAX_ATTEMPTS}")

        # Étape 2 : production
        if entry["attempts"] == 1:
            template = REDACTION_IMPL if config == "redaction" else DEV_IMPL
            prompt = template.format(item_id=item.id, branch=branch, body=item.body)
        else:
            prompt = DEV_FIX.format(logs=logs[-6000:])
        call_agent("dev", MODEL_DEV, prompt, dry_run)

        # Étape 3 : vérificateurs déterministes — le juge
        results = run_all_checks(config, dry_run)
        if not all(r.passed for r in results):
            logs = "\n\n".join(f"### {r.name}\n{r.output}" for r in results if not r.passed)
            log("   → vérifications rouges, retour au dev")
            continue

        # Étape 4 : revue par un modèle différent
        review = call_agent(
            "CTO/revue", MODEL_CTO,
            CTO_REVIEW.format(item_id=item.id, branch=branch),
            dry_run,
        )
        first = (review.strip().splitlines() or [""])[0].upper()
        if dry_run or "APPROUVE" in first:
            log(f"   ✓ {item.id} approuvé")
            entry["state"] = "termine"
            save_state(state)
            return "termine"

        log("   → rejeté en revue, retour au dev")
        logs = review
        time.sleep(2)

    log(f"   ✗ {item.id} bloqué après {MAX_ATTEMPTS} tentatives")
    todoist_task(
        f"[Orchestrateur] {item.id} bloqué après {MAX_ATTEMPTS} tentatives",
        f"Titre : {item.title}\nBranche : {branch}\n\nDerniers logs :\n{logs[-3000:]}",
    )
    entry["state"] = "bloque"
    save_state(state)
    return "bloque"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", choices=["code", "redaction"], required=True)
    ap.add_argument("--plan", type=Path, default=Path("docs/coach-plan.md"))
    ap.add_argument("--queue", type=Path, default=Path("queue/articles.json"))
    ap.add_argument("--lot", help="ne traiter qu'un lot, ex. « Lot 1 »")
    ap.add_argument("--max-items", type=int, default=1,
                    help="nombre de lots par exécution (défaut 1 : un point de contrôle humain entre chaque)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    state = load_state()

    if args.config == "code":
        items = parse_plan(REPO / args.plan)
    else:
        items = load_queue(REPO / args.queue)

    if args.lot:
        items = [i for i in items if i.id.lower() == args.lot.lower()]

    done_ids = {k for k, v in state["items"].items() if v.get("state") == "termine"}
    todo = [
        i for i in items
        if state["items"].get(i.id, {}).get("state") not in {"termine", "bloque"}
        and all(d in done_ids for d in i.depends_on)
    ]

    if not todo:
        log("Rien à traiter (tout terminé, bloqué, ou dépendances non satisfaites).")
        return 0

    log(f"{len(todo)} élément(s) éligible(s), traitement de {min(args.max_items, len(todo))}")

    failures = 0
    for item in todo[: args.max_items]:
        outcome = process_item(item, args.config, state, args.dry_run)
        if outcome == "bloque":
            failures += 1
        save_state(state)

    log("\n=== Fin d'exécution ===")
    for k, v in state["items"].items():
        log(f"  {k}: {v['state']} ({v['attempts']} tentative(s))")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
