"""File éditoriale : état des données, gabarits récurrents, règle de sélection.

Le cœur du système est la règle de sélection : le publieur ne suit pas un
ordre figé. Il regarde l'état réel des données et choisit ce qui est
rédigeable maintenant.

- Un article **patch_courant** ne peut pas être écrit sur un patch trop
  jeune : les premiers jours, les échantillons ne permettent rien de solide.
- Un article **comparatif** le peut, lui, dès le début d'un patch — c'est
  même son sujet (« premières tendances », « vitesse d'adoption »).
- Un article **structurel** ne périme jamais et ne dépend d'aucun patch :
  c'est le réservoir tampon, disponible tous les jours de l'année.

Conséquence : le jour où un patch sort, la file n'est pas vide. Elle bascule
sur le comparatif et le structurel.
"""

import json
import os
import sqlite3
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE_PATH = os.path.join(REPO, "queue", "articles.json")
TEMPLATES_PATH = os.path.join(REPO, "queue", "templates.json")

# Un patch plus jeune que ça n'a pas d'échantillon exploitable par champion.
# Ajustables par variable d'environnement : le bon seuil dépend du débit de
# collecte, qui change quand on ajoute une région ou qu'on bouge les quotas.
MIN_PATCH_AGE_DAYS = float(os.environ.get("QUEUE_MIN_PATCH_AGE_DAYS", "3"))
# En dessous, même après trois jours, il n'y a pas de quoi conclure.
MIN_PATCH_MATCHES = int(os.environ.get("QUEUE_MIN_PATCH_MATCHES", "50000"))

STATUSES = ("en_attente", "donnees_pretes", "redige", "verifie", "publie", "bloque")
MAX_ATTEMPTS = 3
# Statuts qui comptent dans le stock prêt à publier
IN_STOCK = ("verifie",)


# ---------------------------------------------------------------------------
# Chargement / écriture
# ---------------------------------------------------------------------------

def load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_queue() -> dict:
    return load_json(QUEUE_PATH)


def save_queue(queue: dict) -> None:
    tmp = f"{QUEUE_PATH}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(queue, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, QUEUE_PATH)  # atomique : pas de file tronquée


# ---------------------------------------------------------------------------
# Disponibilité des données
# ---------------------------------------------------------------------------

# Chaque donnée citable dans « donnees_requises » ou « debloque_par » a un
# test explicite. Une donnée inconnue n'est JAMAIS considérée comme
# disponible : une faute de frappe doit bloquer l'article, pas le laisser
# passer pour rédigeable.
#
# Chaque requête porte un {cond} : c'est le filtre de patch, et il fait toute
# la différence de portée entre les deux régimes.
#
#   patch_courant / comparatif -> compté SUR LE PATCH COURANT
#   structurel                 -> compté SUR TOUTE LA BASE
#
# Une structurelle ne dépend d'aucun patch : la scoper au patch courant
# éteindrait le réservoir exactement le jour où un patch sort, c'est-à-dire
# le jour où il est le plus utile.
#
# (libellé, requête, volume minimal)
DATA_CHECKS = {
    "matches": (
        "matchs",
        "SELECT COUNT(*) FROM matches WHERE {cond}", 1000),
    "participants": (
        "participants",
        "SELECT COUNT(*) FROM participants WHERE {cond}", 10_000),
    "bans": (
        "bans",
        "SELECT COUNT(*) FROM bans b JOIN matches m ON m.match_id = b.match_id"
        " WHERE {cond} AND b.champion_id > 0", 10_000),
    "team_position": (
        "postes renseignés",
        "SELECT COUNT(*) FROM participants"
        " WHERE {cond} AND team_position IS NOT NULL AND team_position != ''",
        50_000),
    "team_id": (
        "équipes renseignées",
        "SELECT COUNT(*) FROM participants WHERE {cond} AND team_id IS NOT NULL",
        10_000),
    "tier_bucket_source": (
        "buckets de rank",
        "SELECT COUNT(*) FROM matches"
        " WHERE {cond} AND tier_bucket_source IS NOT NULL", 1000),
    "region": (
        "régions couvertes",
        "SELECT COUNT(DISTINCT region) FROM matches WHERE {cond}", 2),
    "timeline_events": (
        "événements de timeline",
        "SELECT COUNT(*) FROM timeline_events e"
        " JOIN matches m ON m.match_id = e.match_id WHERE {cond}", 100_000),
    "timeline_frames": (
        "frames de timeline",
        "SELECT COUNT(*) FROM timeline_frames f"
        " JOIN matches m ON m.match_id = f.match_id WHERE {cond}", 500_000),
    "horde_kills": (
        "voidgrubs renseignés",
        "SELECT COUNT(*) FROM team_objectives o"
        " JOIN matches m ON m.match_id = o.match_id"
        " WHERE {cond} AND o.horde_kills IS NOT NULL", 10_000),
    "team_objectives": (
        "objectifs d'équipe",
        "SELECT COUNT(*) FROM team_objectives o"
        " JOIN matches m ON m.match_id = o.match_id WHERE {cond}", 10_000),
    "patch_precedent": (
        "matchs du patch précédent",
        None, 50_000),   # cas particulier, traité dans DataState
}

# Les tables jointes préfixent leur colonne patch, les autres non.
PATCH_COND = {
    "bans": "m.patch = ?", "timeline_events": "m.patch = ?",
    "timeline_frames": "m.patch = ?", "horde_kills": "m.patch = ?",
    "team_objectives": "m.patch = ?",
}
SCOPE_PATCH = "patch"
SCOPE_ALL = "base"


class DataState:
    """Photographie de l'état des données, prise une fois par exécution."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.available = os.path.exists(db_path)
        self.patch = None
        self.patch_age_days = None
        self.total_matches = 0
        self.previous_patch = None
        self.previous_matches = 0
        # (nom de donnée, portée) -> compte
        self.counts: dict[tuple, int] = {}
        self.usable_cells = None
        if self.available:
            self._probe()

    def _probe(self) -> None:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True)
        try:
            patches = [
                (row[0], row[1], row[2]) for row in conn.execute(
                    "SELECT patch, COUNT(*), MIN(game_creation) FROM matches"
                    " GROUP BY patch")
            ]
            if not patches:
                return
            # Patch courant = le plus grand au sens numérique (16.9 < 16.10)
            def key(p):
                return tuple(int(n) for n in p[0].split(".") if n.isdigit())
            patches.sort(key=key)
            self.patch, self.total_matches, first_seen = patches[-1]
            if len(patches) > 1:
                self.previous_patch, self.previous_matches, _ = patches[-2]
            if first_seen:
                # game_creation est en millisecondes
                age = time.time() - (first_seen / 1000)
                self.patch_age_days = max(0.0, age / 86400)

            for name, (_, sql, _) in DATA_CHECKS.items():
                if sql is None:
                    continue
                cond = PATCH_COND.get(name, "patch = ?")
                for scope, where, params in (
                        (SCOPE_PATCH, cond, (self.patch,)),
                        (SCOPE_ALL, "1 = 1", ())):
                    try:
                        value = conn.execute(
                            sql.format(cond=where), params).fetchone()[0] or 0
                    except sqlite3.Error:
                        value = 0   # table absente = donnée indisponible
                    self.counts[(name, scope)] = value
            self.counts[("patch_precedent", SCOPE_PATCH)] = self.previous_matches
            self.counts[("patch_precedent", SCOPE_ALL)] = self.previous_matches
        finally:
            conn.close()

    def check(self, name: str, scope: str = SCOPE_PATCH) -> tuple[bool, str]:
        """(disponible, explication) pour une donnée requise.

        `scope` vaut SCOPE_PATCH (patch courant) ou SCOPE_ALL (toute la base).
        """
        if not self.available:
            return False, f"{name} : base introuvable ({self.db_path})"
        spec = DATA_CHECKS.get(name)
        if spec is None:
            return False, f"{name} : donnée inconnue (faute de frappe ?)"
        label, _, minimum = spec
        count = self.counts.get((name, scope), 0)
        where = "sur le patch" if scope == SCOPE_PATCH else "en base"
        if count >= minimum:
            return True, f"{label} {where} : {count:,}".replace(",", " ")
        return False, (f"{label} {where} : {count:,} / {minimum:,} attendus"
                       .replace(",", " "))

    def missing(self, names, scope: str = SCOPE_PATCH) -> list[str]:
        return [reason for name in names or []
                for ok, reason in [self.check(name, scope)] if not ok]

    @property
    def patch_mature(self) -> bool:
        """Le patch courant est-il assez vieux ET assez fourni pour porter une
        étude patch_courant ?"""
        if self.patch_age_days is None:
            return False
        return (self.patch_age_days >= MIN_PATCH_AGE_DAYS
                and self.total_matches >= MIN_PATCH_MATCHES)


# ---------------------------------------------------------------------------
# Règle de sélection
# ---------------------------------------------------------------------------

def blockers(article: dict, state: DataState) -> list[str]:
    """Ce qui empêche cet article d'être rédigé maintenant. Vide = rédigeable.

    L'ordre des tests suit leur coût de lecture pour un humain : statut,
    puis régime, puis données.
    """
    reasons = []
    statut = article.get("statut", "en_attente")
    if statut in ("redige", "verifie", "publie"):
        return [f"déjà au statut « {statut} »"]
    if statut == "bloque":
        return [f"bloqué après {article.get('tentatives', 0)} tentatives"]
    if article.get("tentatives", 0) >= MAX_ATTEMPTS:
        return [f"{article['tentatives']} tentatives, à passer en bloque"]

    regime = article.get("regime")
    if regime == "patch_courant":
        # Le seul régime qui exige un patch installé : les premiers jours,
        # aucun échantillon par champion ne tient debout.
        if state.patch_age_days is None:
            reasons.append("âge du patch courant inconnu")
        elif state.patch_age_days < MIN_PATCH_AGE_DAYS:
            reasons.append(
                f"patch {state.patch} trop jeune "
                f"({state.patch_age_days:.1f} j < {MIN_PATCH_AGE_DAYS:g} j)")
        if state.total_matches < MIN_PATCH_MATCHES:
            reasons.append(
                f"volume insuffisant ({state.total_matches:,} / "
                f"{MIN_PATCH_MATCHES:,} matchs)".replace(",", " "))
        cible = article.get("patch_cible")
        if cible and state.patch and cible != state.patch:
            reasons.append(f"vise le patch {cible}, courant : {state.patch}")

    # comparatif : rédigeable dès le début d'un patch, c'est son sujet —
    # il lui faut en revanche un patch précédent à comparer.
    # structurel : aucune contrainte de patch, c'est le réservoir tampon.

    # Portée des données : une structurelle se nourrit de TOUTE la base.
    # La scoper au patch courant l'éteindrait le jour d'un patch, c'est-à-dire
    # au moment précis où le réservoir doit prendre le relais.
    scope = SCOPE_ALL if regime == "structurel" else SCOPE_PATCH
    reasons.extend(state.missing(article.get("donnees_requises"), scope))

    # « debloque_par » : la donnée qu'attend une structurelle. Tant qu'elle
    # n'est pas là en volume, l'article reste invisible à la rédaction ; il
    # le devient de lui-même quand elle arrive.
    gate = article.get("debloque_par")
    if gate:
        names = [gate] if isinstance(gate, str) else list(gate)
        for reason in state.missing(names, scope):
            reasons.append(f"en attente de {reason}")
    return reasons


def writable(articles: list, state: DataState) -> list:
    return [a for a in articles if not blockers(a, state)]


def stock_count(articles: list) -> int:
    return sum(1 for a in articles if a.get("statut") in IN_STOCK)


def selection_order(articles: list, state: DataState, stock: int,
                    stock_min: int) -> list:
    """Ordonne les articles rédigeables par priorité.

    Quand le stock est bas, le structurel passe devant : il ne périme pas,
    il se rédige d'avance, et il est le seul disponible les jours où un
    patch vient de sortir. Remplir le tampon d'abord, c'est ce qui évite
    d'être à sec au prochain patch.
    """
    low = stock < stock_min

    def rank(article):
        regime = article.get("regime")
        if low:
            priority = {"structurel": 0, "comparatif": 1, "patch_courant": 2}
        else:
            priority = {"patch_courant": 0, "comparatif": 1, "structurel": 2}
        return (priority.get(regime, 3), article.get("jour_prefere", 9),
                article.get("id", ""))

    return sorted(writable(articles, state), key=rank)


# ---------------------------------------------------------------------------
# Instanciation des gabarits récurrents
# ---------------------------------------------------------------------------

def patch_to_slug(patch: str) -> str:
    return patch.replace(".", "-")


def _fill(text: str, patch: str, variant: dict) -> str:
    out = (text or "").replace("{patch-slug}", patch_to_slug(patch))
    out = out.replace("{patch}", patch)
    for key, value in variant.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def instantiate(templates: list, patch: str) -> list:
    """Articles à créer pour ce patch, un par gabarit (et par variante)."""
    created = []
    for tpl in templates:
        variants = tpl.get("variantes") or [{}]
        for variant in variants:
            suffix = variant.get("role_slug")
            article_id = "-".join(
                filter(None, [tpl["id"], suffix, patch_to_slug(patch)]))
            created.append({
                "id": article_id,
                "slug": _fill(tpl["slug_template"], patch, variant),
                "famille": tpl["famille"],
                "titre": _fill(tpl["titre_template"], patch, variant),
                "angle": _fill(tpl["angle"], patch, variant),
                "regime": tpl.get("regime", "patch_courant"),
                "donnees_requises": list(tpl.get("donnees_requises") or []),
                "patch_cible": patch,
                "jour_prefere": variant.get("jour_prefere",
                                            tpl.get("jour_prefere", 9)),
                "statut": "en_attente",
                "tentatives": 0,
                "gabarit": tpl["id"],
            })
    return created


def sync_templates(queue: dict, templates: list, patch: str) -> list:
    """Ajoute les articles manquants pour ce patch. Idempotent : relancer ne
    duplique rien et ne touche pas au statut de ce qui existe déjà."""
    existing = {a["id"] for a in queue.get("articles", [])}
    fresh = [a for a in instantiate(templates, patch) if a["id"] not in existing]
    queue.setdefault("articles", []).extend(fresh)
    return fresh
