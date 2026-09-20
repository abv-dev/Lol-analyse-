#!/usr/bin/env python3
"""Générateur d'études EloLab : de matches.db à une étude publiée.

    python3 scripts/generate_study.py daily      # sujet du jour, prose claude -p
    python3 scripts/generate_study.py tierlist   # tier list du patch, sans LLM
    python3 scripts/generate_study.py daily --dry-run   # les 14 prochains sujets
    options : --no-push  --no-git  --skip-build  --article <id>  --db <chemin>

Séquence, chaque étape pouvant arrêter le job SANS RIEN PUBLIER :

  1. dépôt propre et à jour (git pull --ff-only)
  2. purge en cours ? (verrou data/.purge.lock) -> arrêt
  3. lecture seule de matches.db (mode=ro) : patch courant, couverture
  4. GATES : >= 20 000 matchs par région analysée, patch mûr (Lot 3)
  5. sujet : la file éditoriale (queue/articles.json, instanciée depuis
     queue/templates.json) dans l'ordre de lolcollector.editorial —
     premier article éligible non publié sur le patch courant, dont
     l'échantillon suffit ; sinon on passe au suivant, et si aucun ne
     convient le job s'arrête sans rien publier
  6. calcul des faits (chiffres, tableaux, graphiques : le code)
  7. prose : claude -p reçoit le précis, écrit avec des repères {{id}} ;
     tout chiffre en dur ou repère inconnu est rejeté (3 tentatives)
  8. écriture MDX + JSON, puis scripts/verify_generated.py (recalcul
     indépendant) et, pour la tier list, scripts/verify_study.py
  9. npm run build
 10. état (studies/state.json), commit, push -> Vercel déploie

Codes de sortie : 0 publié ou rien à faire · 2 gate non franchi ·
3 vérification ou rédaction en échec · 4 verrou/purge en cours ·
5 build ou git en échec · 1 erreur inattendue. Tout échec après écriture
supprime les fichiers créés.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import traceback

CODE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE)
# Racine du dépôt où lire/écrire les études ; surchargeable pour les tests.
REPO = os.environ.get("ELOLAB_REPO", CODE)

from lolcollector import editorial, studygen as sg  # noqa: E402
from lolcollector.studytopics import BUILDERS  # noqa: E402
from lolcollector.export import MIN_REGION_MATCHES, fetch_champion_names  # noqa: E402

STATE_PATH = os.path.join(REPO, "studies", "state.json")
CONTENT_ROOT = os.path.join(REPO, "site", "content", "etudes")
WRITER_MODEL = os.environ.get("ELOLAB_WRITER_MODEL", "claude-opus-5")
CLAUDE_TIMEOUT = int(os.environ.get("ELOLAB_CLAUDE_TIMEOUT", "900"))
MAX_ATTEMPTS = 3
MIN_AGE_DAYS = float(os.environ.get("QUEUE_MIN_PATCH_AGE_DAYS", "3"))
MIN_TOTAL = int(os.environ.get("QUEUE_MIN_PATCH_MATCHES", "50000"))


class Stop(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def run(cmd, cwd=REPO, timeout=1800, check=True, **kw):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", timeout=timeout, **kw)
    if check and proc.returncode != 0:
        raise Stop(5, f"commande en échec ({' '.join(cmd)}) :\n"
                      f"{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc


# ---------------------------------------------------------------------------
# Verrou de purge : lecture sous verrou partagé, la purge (exclusif) attend
# ---------------------------------------------------------------------------

class PurgeGuard:
    def __init__(self, db_path):
        self.path = os.path.join(os.path.dirname(os.path.abspath(db_path)), ".purge.lock")
        self.fh = None

    def __enter__(self):
        try:
            import fcntl
        except ImportError:  # Windows (tests locaux) : pas de purge concurrente
            return self
        self.fh = open(self.path, "a")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            self.fh.close()
            raise Stop(4, "purge de la base en cours : aucune lecture, nouvel essai au prochain passage")
        return self

    def __exit__(self, *exc):
        if self.fh:
            self.fh.close()


# ---------------------------------------------------------------------------
# État et sujets
# ---------------------------------------------------------------------------

def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def published_on(state, topic, patch):
    return any(e["topic"] == topic and e["patch"] == patch for e in state["published"])


def published_titles(patch: str) -> set:
    """Titres déjà en ligne pour ce patch : deux études ne peuvent pas porter
    le même titre le même patch."""
    titles = set()
    for family in sorted(os.listdir(CONTENT_ROOT)) if os.path.isdir(CONTENT_ROOT) else []:
        meta_path = os.path.join(CONTENT_ROOT, family, sg.patch_to_slug(patch), "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as fh:
                titles.add(json.load(fh).get("title"))
    return titles


def queue_for(db_path: str):
    """File éditoriale du patch courant : (état des données, file, articles).

    L'instanciation des gabarits se fait EN MÉMOIRE. queue/articles.json n'est
    écrit qu'au moment de publier : un job qui ne publie rien ne doit pas
    laisser le dépôt modifié, sinon le job suivant refuserait de démarrer.
    """
    state = editorial.DataState(db_path)
    queue = editorial.load_queue()
    if state.patch:
        editorial.sync_templates(queue, editorial.load_json(
            editorial.TEMPLATES_PATH)["templates"], state.patch)
    return state, queue, queue.get("articles", [])


def candidates_for(articles, data_state):
    """Articles éligibles, dans l'ordre de la règle de sélection existante."""
    stock = editorial.stock_count(articles)
    return editorial.selection_order(articles, data_state, stock, stock_min=0)


# ---------------------------------------------------------------------------
# Rédaction
# ---------------------------------------------------------------------------

def claude_command():
    custom = os.environ.get("ELOLAB_CLAUDE_CMD")
    return json.loads(custom) if custom else ["claude"]


def build_prompt(plan, precis, feedback):
    with open(os.path.join(REPO, "docs", "editorial.md"), encoding="utf-8") as fh:
        charte = fh.read()
    with open(os.path.join(REPO, "site", "content", "etudes", "tierlist", "16-15",
                           "index.mdx"), encoding="utf-8") as fh:
        modele = fh.read()
    prompt = f"""Tu rédiges la prose d'une étude statistique du site EloLab, en français.

RÈGLE ABSOLUE — LES CHIFFRES
Tu n'écris AUCUN chiffre. Chaque nombre est cité par son repère entre doubles accolades,
tel qu'il apparaît dans le précis : {{{{kaisa.global.wr}}}} donne « 49,80 % ». Pour un winrate
avec son intervalle, écris {{{{kaisa.global:stat}}}} (identifiant de base, sans .wr).
Seules exceptions : « 50 % » et « 95 % ». Tout autre chiffre écrit en toutes lettres
ou en chiffres (« trois points », « 12 champions ») est interdit : utilise un repère ou
une formulation sans nombre. Un repère ne se cite que dans une phrase qui nomme le
champion concerné. N'invente aucun repère.

CE QUE TU PRODUIS (JSON imposé)
- description : une phrase factuelle (moins de 260 caractères) pour la carte de l'étude.
- chapo : le résultat principal en deux à quatre phrases ; commence par une phrase en **gras**.
- sections : pour chaque section du précis, dans le même ordre et avec la même key :
  un titre qui énonce le résultat mesuré (sans repère, sans chiffre), et un texte d'un à
  trois paragraphes. Le texte est placé sous le chiffre-clé et au-dessus du tableau
  généré par le code : commente le tableau, ne le recopie pas en entier.
- retenir : un paragraphe de conclusion.
Pas de titre markdown, pas de liste, pas de composant, pas de caractères < {{ }}.

RESPECTE LA CHARTE ÉDITORIALE CI-DESSOUS, en particulier : un écart dont les intervalles
se recouvrent n'est pas un classement (les champs « lecture » et « ecart_net » du précis
tranchent pour toi) ; aucune causalité (« grâce à », « parce que », « explique »…) ;
aucun conseil ; aucun build, rune, matchup ni objet ; pas de superlatif non mesuré ;
pas de tutoiement ; ton sobre et factuel, pas d'émoji. N'écris jamais le nom du jeu.

=== CHARTE ===
{charte}

=== ARTICLE MODÈLE (ton et structure uniquement : ses chiffres ne concernent pas ce patch) ===
{modele}

=== ÉTUDE À RÉDIGER ===
Titre : {plan.title}
Angle : {plan.angle}

=== PRÉCIS (seule source de vérité) ===
{json.dumps(precis, ensure_ascii=False, indent=1)}
"""
    if feedback:
        prompt += ("\n=== TENTATIVE PRÉCÉDENTE REJETÉE ===\nCorrige le texte, sans contourner "
                   "les contrôles :\n" + "\n".join(f"- {e}" for e in feedback[:40]) + "\n")
    return prompt


def write_prose(plan, precis, feedback):
    prompt = build_prompt(plan, precis, feedback)
    workdir = os.path.join(REPO, "logs", "claude-work")
    os.makedirs(workdir, exist_ok=True)
    cmd = claude_command() + [
        "-p", "--model", WRITER_MODEL, "--output-format", "json",
        "--json-schema", json.dumps(sg.prose_schema(plan)),
        "--tools", "", "--no-session-persistence",
    ]
    try:
        proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                              encoding="utf-8", timeout=CLAUDE_TIMEOUT, cwd=workdir)
    except subprocess.TimeoutExpired:
        return None, ["claude -p : délai dépassé"], 0.0
    if proc.returncode != 0:
        return None, [f"claude -p code {proc.returncode} : {proc.stderr[-500:]}"], 0.0
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        return None, [f"claude -p : sortie non JSON : {proc.stdout[:300]}"], 0.0
    cost = float(out.get("total_cost_usd") or 0)
    if out.get("is_error") or not isinstance(out.get("structured_output"), dict):
        return None, [f"claude -p : pas de sortie structurée ({out.get('subtype')})"], cost
    return out["structured_output"], [], cost


# ---------------------------------------------------------------------------
# Écriture, vérification, publication
# ---------------------------------------------------------------------------

def paths_for(plan):
    rel_content = os.path.join("site", "content", "etudes", plan.family, plan.slug)
    rel_data = os.path.join("site", "data", "etudes", plan.family, plan.slug)
    return rel_content, rel_data


def write_study(plan, model):
    rel_content, rel_data = paths_for(plan)
    content, data = os.path.join(REPO, rel_content), os.path.join(REPO, rel_data)
    mdx = sg.render_mdx(plan)  # peut ajouter les faits de la phrase de lecture
    ds = plan.facts.ds
    # Les données que le vérificateur devra relire : uniquement les métriques
    # et les événements réellement cités par l'étude.
    metrics = sorted({f["spec"]["metric"] for f in plan.facts.items.values()
                      if "metric" in f["spec"]})
    events = sorted({f["spec"]["event"] for f in plan.facts.items.values()
                     if "event" in f["spec"]})
    if metrics:
        sg.write_json(os.path.join(data, "metrics.json"),
                      {m: ds.metric_rows(m) for m in metrics}, compact=True)
    if events:
        sg.write_json(os.path.join(data, "timeline.json"),
                      {e: ds.timeline_rows(e) for e in events}, compact=True)
    sg.write_json(os.path.join(data, "tierlist.json"), ds.rows)
    sg.write_json(os.path.join(data, "tierlist-roles.json"), ds.role_rows, compact=True)
    sg.write_json(os.path.join(data, "meta.json"), ds.export_meta(getattr(ds, "ddragon_version", None)))
    sg.write_json(os.path.join(data, "study.json"), sg.study_payload(plan, model))
    sg.write_json(os.path.join(content, "meta.json"), sg.content_meta(plan))
    with open(os.path.join(content, "index.mdx"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(mdx)
    return rel_content, rel_data


def verify(plan, rel_content):
    checks = [[sys.executable, os.path.join("scripts", "verify_generated.py"), rel_content]]
    if plan.family == "tierlist":
        checks.append([sys.executable, os.path.join("scripts", "verify_study.py"), rel_content])
    errors = []
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    for cmd in checks:
        proc = run(cmd, check=False, env=env)
        log(f"{os.path.basename(cmd[1])} : code {proc.returncode}")
        if proc.returncode != 0:
            errors.append((proc.stdout + proc.stderr).strip()[-3000:])
    return errors


def build_site():
    site = os.path.join(REPO, "site")
    npm = shutil.which("npm") or "npm"
    if not os.path.isdir(os.path.join(site, "node_modules")):
        log("npm ci…")
        run([npm, "ci", "--no-audit", "--no-fund"], cwd=site, timeout=1800)
    log("npm run build…")
    run([npm, "run", "build"], cwd=site, timeout=1800)


def probe(ds, article):
    """Construit le plan d'un article. (plan, None) ou (None, raison)."""
    builder = BUILDERS.get(article.get("generateur"))
    if builder is None:
        return None, f"générateur inconnu : {article.get('generateur')}"
    try:
        plan = builder(ds, article)
    except sg.NotFeasible as exc:
        return None, f"échantillon insuffisant — {exc}"
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        return None, f"calcul impossible — {type(exc).__name__}: {exc}"
    return plan, None


def dry_run(ds, data_state, articles, patch, limit):
    """Liste les prochains sujets et leur éligibilité. N'écrit rien."""
    ordered = candidates_for(articles, data_state)
    titles = published_titles(patch)
    blocked = [(a, editorial.blockers(a, data_state)) for a in articles]
    blocked = [(a, r) for a, r in blocked if r]
    log(f"file du patch {patch} : {len(articles)} articles, {len(ordered)} éligibles, "
        f"{len(blocked)} bloqués")
    print(f"\n{'#':>2}  {'article':38} {'sujet':56} état")
    print("-" * 132)
    shown = 0
    for article in ordered:
        if shown >= limit:
            break
        shown += 1
        rel = os.path.join("site", "content", "etudes", *article["slug"].split("/"))
        if os.path.exists(os.path.join(REPO, rel)):
            etat = "déjà publié (dossier présent)"
        elif article["titre"] in titles:
            etat = "titre déjà utilisé sur ce patch"
        else:
            plan, reason = probe(ds, article)
            etat = (f"réalisable — {len(plan.facts.items)} faits, "
                    f"{len(plan.sections)} sections") if plan else f"NON — {reason}"
        print(f"{shown:>2}  {article['id']:38} {article['titre'][:56]:56} {etat}")
    if blocked:
        print(f"\nBloqués par les données ({len(blocked)}) :")
        for article, reasons in blocked[:12]:
            print(f"    {article['id']:38} {reasons[0]}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["daily", "tierlist", "weekly"],
                    help="daily : le sujet du jour ; tierlist : la tier list du patch")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", os.path.join(REPO, "data", "matches.db")))
    ap.add_argument("--article", help="forcer un article de la file (les gates s'appliquent)")
    ap.add_argument("--dry-run", nargs="?", type=int, const=14, default=None,
                    metavar="N", help="liste les N prochains sujets (défaut 14) et leur "
                                      "éligibilité, sans rien écrire")
    ap.add_argument("--no-push", action="store_true", help="commit local, sans push")
    ap.add_argument("--no-git", action="store_true", help="aucune opération git (tests)")
    ap.add_argument("--skip-build", action="store_true")
    args = ap.parse_args(argv)
    mode = "daily" if args.mode == "weekly" else args.mode

    created = []
    state_backup = queue_backup = None
    try:
        # 1. dépôt propre et à jour
        if not args.no_git and args.dry_run is None:
            dirty = run(["git", "status", "--porcelain", "--", "site/content", "site/data",
                         "studies", "queue"]).stdout.strip()
            if dirty:
                raise Stop(5, f"dépôt modifié localement, rien n'est tenté :\n{dirty}")
            run(["git", "pull", "--ff-only", "--quiet"], timeout=300)

        state = load_json(STATE_PATH, {"published": []})

        # 2-3. lecture seule sous verrou partagé de la purge
        with sg_readonly(args.db) as conn:
            patch = sg.current_patch(conn)
            if not patch:
                raise Stop(2, "aucun match en base")
            cov = sg.coverage(conn, patch)
            log(f"patch {patch} : " + ", ".join(f"{r} {n}" for r, n in cov["per_region"].items())
                + f", total {cov['total']}")

            # 4. gates de couverture et de maturité
            reasons = sg.check_gates(cov, MIN_REGION_MATCHES, MIN_AGE_DAYS, MIN_TOTAL)
            if reasons:
                raise Stop(2, "gates non franchis, rien n'est publié :\n  - "
                              + "\n  - ".join(reasons))
            log("gates franchis")

            log("calcul des agrégats (lecture seule)…")
            ds = sg.Dataset.load(conn, patch, None)
            row = conn.execute(
                "SELECT value FROM meta WHERE key = 'ddragon_current'").fetchone()
            ddragon = row[0] if row and row[0].startswith(patch + ".") else f"{patch}.1"
            names = {} if os.environ.get("ELOLAB_OFFLINE") else fetch_champion_names(ddragon)
            for r in ds.rows:
                if r["champion_id"] in names:
                    r["champion_name"] = names[r["champion_id"]]
            ds = sg.Dataset(ds.patch, ds.rows, ds.role_rows, ds.cells, ds.first, ds.last)
            ds.conn = conn                     # métriques et timelines à la demande
            ds.ddragon_version = ddragon if names else None

            # 5. le sujet du jour vient de la file éditoriale
            plan = article = None
            if mode == "tierlist":
                rel = os.path.join("site", "content", "etudes", "tierlist",
                                   sg.patch_to_slug(patch))
                if os.path.exists(os.path.join(REPO, rel)):
                    log(f"tier list du patch {patch} déjà publiée : rien à faire")
                    return 0
                article = {"id": "tierlist-globale", "gabarit": "tierlist-globale"}
                plan = sg.plan_tierlist(ds)
                queue = None
            else:
                data_state, queue, articles = queue_for(args.db)
                if data_state.patch != patch:
                    log(f"! la file voit le patch {data_state.patch}, la lecture {patch}")
                if args.dry_run is not None:
                    return dry_run(ds, data_state, articles, patch, args.dry_run)
                candidates = candidates_for(articles, data_state)
                log(f"file : {len(articles)} articles instanciés, "
                    f"{len(candidates)} éligibles aujourd'hui")
                if args.article:
                    candidates = [a for a in candidates if a["id"] == args.article]
                    if not candidates:
                        raise Stop(2, f"{args.article} : inconnu ou non éligible aujourd'hui")
                titles = published_titles(patch)
                for candidate in candidates:
                    rel = os.path.join("site", "content", "etudes",
                                       *candidate["slug"].split("/"))
                    if os.path.exists(os.path.join(REPO, rel)):
                        log(f"  {candidate['id']} : déjà publié, sujet suivant")
                        candidate["statut"] = "publie"
                        continue
                    if candidate["titre"] in titles:
                        log(f"  {candidate['id']} : titre déjà utilisé ce patch, sujet suivant")
                        continue
                    plan, reason = probe(ds, candidate)
                    if plan is None:
                        log(f"  {candidate['id']} : {reason}")
                        continue
                    article = candidate
                    break
            if plan is None:
                log("aucun sujet éligible et réalisable aujourd'hui : rien n'est publié")
                return 0
            log(f"sujet retenu : {article['id']} -> {plan.family}/{plan.slug}")
            precis = sg.build_precis(plan) if plan.prose else None

        ds.conn = None   # la base est refermée : plus aucun chargement possible

        # 7. prose
        cost = 0.0
        attempts = 0
        if plan.prose:
            digest = hashlib.sha256(json.dumps(precis, sort_keys=True).encode()).hexdigest()[:16]
            log(f"précis {digest} : {len(plan.facts.items)} faits")
            feedback = []
            for attempts in range(1, MAX_ATTEMPTS + 1):
                log(f"rédaction, tentative {attempts}/{MAX_ATTEMPTS} ({WRITER_MODEL})…")
                prose, errors, c = write_prose(plan, precis, feedback)
                cost += c
                if not errors:
                    errors = sg.apply_prose(plan, prose)
                if not errors:
                    break
                log(f"tentative {attempts} rejetée ({len(errors)} problème(s)) :\n  - "
                    + "\n  - ".join(errors[:15]))
                feedback = errors
            else:
                raise Stop(3, f"rédaction rejetée {MAX_ATTEMPTS} fois, rien n'est publié")

        # 8. écriture et vérification
        rel_content, rel_data = paths_for(plan)
        for rel in (rel_content, rel_data):
            if not os.path.exists(os.path.join(REPO, rel)):
                created.append(rel)
        write_study(plan, WRITER_MODEL)
        errors = verify(plan, rel_content)
        if errors:
            raise Stop(3, "vérification des chiffres en échec, rien n'est publié :\n" + "\n".join(errors))

        # 9. build
        if not args.skip_build:
            build_site()

        # 10. état + git
        state_backup = json.dumps(state)
        state["published"].append({
            "topic": article["id"], "gabarit": article.get("gabarit"), "patch": patch,
            "path": rel_content.replace(os.sep, "/"),
            "date": time.strftime("%Y-%m-%d"), "mode": mode,
            "attempts": attempts, "cost_usd": round(cost, 4),
            "writer_model": WRITER_MODEL if plan.prose else None,
        })
        sg.write_json(STATE_PATH, state)
        if queue is not None:
            queue_backup = json.dumps(queue)
            article["statut"] = "publie"
            article["publie_le"] = time.strftime("%Y-%m-%d")
            article["chemin"] = rel_content.replace(os.sep, "/")
            editorial.save_queue(queue)
        log(f"étude écrite : {rel_content} (coût rédaction {cost:.2f} $)")
        if not args.no_git:
            paths = [rel_content, rel_data, os.path.relpath(STATE_PATH, REPO)]
            if queue is not None:
                paths.append(os.path.relpath(editorial.QUEUE_PATH, REPO))
            run(["git", "add", "--", *paths])
            message = (f"Étude automatique : {plan.title}\n\n"
                       f"Sujet {article['id']}, patch {patch}, {ds.total_matches} matchs. "
                       f"Généré par scripts/generate_study.py ({args.mode}) ; "
                       f"chiffres vérifiés par scripts/verify_generated.py.")
            run(["git", "commit", "--quiet", "-m", message])
            created = []  # commité : plus de retour arrière sur fichiers
            if not args.no_push:
                push = run(["git", "push", "--quiet", "origin", "HEAD:main"], check=False, timeout=300)
                if push.returncode != 0:
                    run(["git", "pull", "--rebase", "--quiet"], timeout=300)
                    run(["git", "push", "--quiet", "origin", "HEAD:main"], timeout=300)
                log("poussé sur main : Vercel déploie")
            else:
                log("commit local (--no-push)")
        return 0

    except Stop as stop:
        log(f"ARRÊT (code {stop.code}) : {stop}")
        _rollback(created, state_backup, queue_backup)
        return stop.code
    except Exception:  # noqa: BLE001
        log("ERREUR INATTENDUE :\n" + traceback.format_exc())
        _rollback(created, state_backup, queue_backup)
        return 1


class sg_readonly:
    def __init__(self, db_path):
        self.db_path = db_path
        self.guard = PurgeGuard(db_path)
        self.conn = None

    def __enter__(self):
        self.guard.__enter__()
        try:
            self.conn = sg.open_readonly(self.db_path)
        except Exception:
            self.guard.__exit__()
            raise
        return self.conn

    def __exit__(self, *exc):
        if self.conn:
            self.conn.close()
        self.guard.__exit__()


def _rollback(created, state_backup, queue_backup=None):
    for rel in created:
        shutil.rmtree(os.path.join(REPO, rel), ignore_errors=True)
        log(f"supprimé : {rel}")
    if state_backup is not None:
        with open(STATE_PATH, "w", encoding="utf-8") as fh:
            fh.write(state_backup)
    if queue_backup is not None:
        with open(editorial.QUEUE_PATH, "w", encoding="utf-8") as fh:
            fh.write(queue_backup)


if __name__ == "__main__":
    sys.exit(main())
