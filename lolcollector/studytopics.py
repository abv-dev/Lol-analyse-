"""Constructeurs de sujets d'études.

Sept constructeurs génériques, paramétrés par la file éditoriale
(`queue/templates.json` → champ `generateur` et `params`). Un même
constructeur produit la méta du poste Top et celle du bucket Diamant+ :
seule la portée change.

Chaque constructeur lève `NotFeasible` quand l'échantillon du sujet ne
suffit pas ; `scripts/generate_study.py` passe alors au sujet suivant et ne
publie rien de maigre.
"""

from __future__ import annotations

from .studygen import (
    BUCKET_LABELS, GLOBAL, METRICS, REGION_LABELS, REGION_SHORT, ROLE_LABELS,
    VERDICT, Facts, NotFeasible, Section, StudyPlan, TL_EVENTS,
    _row_precis, _wr_ids, chart, md_table, significance,
)

BUCKETS = ("IRON_BRONZE", "SILVER_GOLD", "PLAT_EMERALD", "DIAMOND_PLUS")
REGIONS = ("europe", "asia", "americas")


# ---------------------------------------------------------------------------
# Portées
# ---------------------------------------------------------------------------

def scope_of(params: dict) -> dict:
    scope = {}
    if params.get("role"):
        scope["role"] = params["role"]
    if params.get("bucket"):
        scope["buckets"] = [params["bucket"]]
    if params.get("region"):
        scope["regions"] = [params["region"]]
    return scope


def scope_label(params: dict) -> str:
    parts = []
    if params.get("role"):
        parts.append(f"au poste {ROLE_LABELS[params['role']]}")
    if params.get("bucket"):
        parts.append(f"en {BUCKET_LABELS[params['bucket']]}")
    if params.get("region"):
        parts.append(f"en {REGION_LABELS[params['region']]}")
    return ", ".join(parts) if parts else "toutes régions, tous ranks"


def sub(scope: dict, **extra) -> dict:
    """Portée dérivée : ajoute un bucket ou une région à la portée courante."""
    out = dict(scope)
    out.update(extra)
    return out


def _plan(ds, article, facts) -> StudyPlan:
    return StudyPlan(article["id"], article["slug"].split("/")[0], article["titre"],
                     article.get("angle", ""), list(article.get("tags") or []),
                     facts, [])


def _eligible(ds, scope, min_games):
    return [c for c in ds.champions() if ds.stats(c, scope)[0] >= min_games]


def _wr_rows(f: Facts, champs, scope, label, with_pick=True):
    """Lignes de tableau (champion, winrate, parties, pick) et leur précis."""
    rows, precis, bases = [], [], []
    for cid in champs:
        base = f.winrate(cid, scope, label)
        ids = _wr_ids(base)
        cells = [f.cell(base), f.d(base + ".games")]
        if with_pick:
            pick = f.rate(cid, scope, "pick_rate", label)
            ids.append(pick)
            cells.append(f.d(pick))
        verdict = VERDICT[significance(f.ds.wr(cid, scope))]
        rows.append([f.ds.names[cid], *cells, verdict])
        precis.append(_row_precis(f, cid, ids, lecture=verdict))
        bases.append((f.ds.names[cid], base))
    return rows, precis, bases


def _counts_section(f: Facts, scope, min_games, key, consigne, label):
    ids = {}
    for cond, texte in (("above", "au-dessus de 50 %"), ("below", "en dessous de 50 %"),
                        ("contains", "indistinguables de 50 %")):
        ids[cond] = f.add(
            f"count.{key}.{cond}",
            {"kind": "count", "scopes": [scope], "min_games": min_games,
             "condition": cond},
            f"champions joués au moins le seuil de parties ({label}) et {texte}")
    seuil = f.param(f"seuil_{key}", min_games, f"parties minimum ({label})")
    return Section(
        key, consigne,
        [f.key_value(ids["above"],
                     f"Champions dont l'intervalle est entièrement au-dessus de 50 % "
                     f"{label}, parmi ceux joués au moins {f.d(seuil)} fois.",
                     unit="champions")],
        [md_table(["Lecture", "Champions"],
                  [["Au-dessus de 50 %", f.d(ids["above"])],
                   ["En dessous de 50 %", f.d(ids["below"])],
                   ["Indistinguables de 50 %", f.d(ids["contains"])]])],
        {"faits_globaux": {i: f"{f.d(i)} — {f.items[i]['meaning']}"
                           for i in [*ids.values(), seuil]}})


def _split_section(f: Facts, plan, champs, scope, key, dims, dim_labels, consigne,
                   titre_graphique):
    """Section « le même champion selon le bucket (ou la région) »."""
    rows, precis = [], []
    for cid in champs:
        cells, ids = [], []
        for dim in dims:
            s = sub(scope, **dim["scope"])
            if f.ds.stats(cid, s)[0] < dim.get("min_games", 300):
                cells.append("échantillon insuffisant")
                continue
            base = f.winrate(cid, s, dim["label"])
            cells.append(f"{f.cell(base)} ({f.d(base + '.games')})")
            ids += _wr_ids(base)
        if not ids:
            continue
        rows.append([f.ds.names[cid], *cells])
        precis.append(_row_precis(f, cid, ids))
    if not rows:
        raise NotFeasible("aucun champion mesurable sur tous les découpages")
    return Section(key, consigne, [], [md_table(["Champion", *dim_labels], rows)],
                   {"lignes": precis})


BUCKET_DIMS = [{"scope": {"buckets": [b]}, "label": BUCKET_LABELS[b]} for b in BUCKETS]
REGION_DIMS = [{"scope": {"regions": [r]}, "label": REGION_LABELS[r]} for r in REGIONS]


# ---------------------------------------------------------------------------
# 1. Classement par winrate (méta d'un poste, d'un bucket, d'une région)
# ---------------------------------------------------------------------------

def wr_ranking(ds, article) -> StudyPlan:
    params = article.get("params") or {}
    scope, label = scope_of(params), scope_label(params)
    min_games = params.get("min_games", 1500)
    f = Facts(ds)
    f.param("min_games", min_games, f"parties minimum {label}")
    base_champs = _eligible(ds, scope, min_games)
    if len(base_champs) < 12:
        raise NotFeasible(f"{len(base_champs)} champions au-dessus de {min_games} "
                          f"parties {label} (12 attendus)")
    plan = _plan(ds, article, f)

    best = sorted(base_champs, key=lambda c: -ds.wr(c, scope)["lo"])[:8]
    rows, precis, bases = _wr_rows(f, best, scope, label)
    s1 = Section(
        "meilleurs",
        f"Les meilleurs winrates {label}, classés par la borne basse de leur "
        "intervalle de confiance : la valeur que l'échantillon garantit, pas la plus "
        "flatteuse. Le champ « lecture » tranche pour chaque champion.",
        [f.key_figure(f.winrate(best[0], scope, label),
                      f"Winrate de {ds.names[best[0]]} {label}, meilleure borne basse "
                      f"d'intervalle de la sélection.")],
        [md_table(["Champion", "Winrate (IC 95 %)", "Parties", "Pick rate", "Lecture"], rows),
         chart(f, plan.charts, bases, "meilleurs", f"Meilleurs winrates {label}", label)],
        {"lignes": precis})

    played = sorted(base_champs, key=lambda c: -ds.stats(c, scope)[0])[:8]
    rows, precis, _ = _wr_rows(f, played, scope, label)
    s2 = Section(
        "plus-joues",
        f"Les champions les plus joués {label} et la lecture de leur winrate. "
        "Comparer avec la section précédente sans en tirer de cause : on décrit des "
        "choix et des résultats, pas un mécanisme.",
        [f.key_value(f.rate(played[0], scope, "pick_rate", label),
                     f"Pick rate de {ds.names[played[0]]}, le champion le plus joué "
                     f"{label}.")],
        [md_table(["Champion", "Winrate (IC 95 %)", "Parties", "Pick rate", "Lecture"], rows)],
        {"lignes": precis})

    if params.get("bucket") or params.get("region"):
        dims, dim_labels, key = REGION_DIMS, [d["label"] for d in REGION_DIMS], "regions"
        consigne = ("Les mêmes champions région par région. Un écart dont les "
                    "intervalles se recouvrent n'est pas un classement.")
        if params.get("region"):
            dims, dim_labels, key = BUCKET_DIMS, [d["label"] for d in BUCKET_DIMS], "ranks"
            consigne = ("Les mêmes champions bucket par bucket. Rappeler que le bucket "
                        "est celui du joueur échantillonné, pas le rang moyen de la partie.")
    else:
        dims, dim_labels, key = BUCKET_DIMS, [d["label"] for d in BUCKET_DIMS], "ranks"
        consigne = ("Les mêmes champions bucket par bucket : un winrate global mélange "
                    "des populations qui ne jouent pas le même jeu. Rappeler que le "
                    "bucket est celui du joueur échantillonné.")
    s3 = _split_section(f, plan, best[:6], scope, key, dims, dim_labels, consigne, "")
    plan.sections = [s1, s2, s3]
    return plan


# ---------------------------------------------------------------------------
# 2. Champions pièges / champions qui surperforment
# ---------------------------------------------------------------------------

def wr_outliers(ds, article) -> StudyPlan:
    params = article.get("params") or {}
    scope, label = scope_of(params), scope_label(params)
    direction = params.get("direction", "below")
    min_games = params.get("min_games", 4000)
    min_pick = params.get("min_pick", 0.03)
    f = Facts(ds)
    f.param("min_games", min_games, f"parties minimum {label}")
    total = ds.matches(scope)
    popular = [c for c in ds.champions()
               if ds.stats(c, scope)[0] >= min_games
               and ds.stats(c, scope)[0] / total >= min_pick]
    picked = [c for c in popular if significance(ds.wr(c, scope)) == direction]
    if len(picked) < 3:
        raise NotFeasible(f"{len(picked)} champions {('sous' if direction == 'below' else 'au-dessus de')} "
                          f"50 % parmi les plus joués {label} (3 attendus)")
    picked.sort(key=lambda c: -ds.stats(c, scope)[0])
    plan = _plan(ds, article, f)

    rows, precis, bases = [], [], []
    for cid in picked[:10]:
        base = f.winrate(cid, scope, label)
        pick = f.rate(cid, scope, "pick_rate", label)
        rows.append([ds.names[cid], f.d(pick), f.cell(base), f.d(base + ".games")])
        precis.append(_row_precis(f, cid, [pick, *_wr_ids(base)]))
        bases.append((ds.names[cid], base))
    cote = ("entièrement sous 50 %" if direction == "below"
            else "entièrement au-dessus de 50 %")
    s1 = Section(
        "liste",
        f"Les champions très joués {label} dont l'intervalle de confiance est {cote}. "
        "Ce sont des mesures de parties gagnées ou perdues, pas un jugement sur le "
        "champion, et surtout pas un conseil de le jouer ou de l'éviter.",
        [f.key_figure(f.winrate(picked[0], scope, label),
                      f"Winrate de {ds.names[picked[0]]}, le plus joué des champions "
                      f"dont l'intervalle est {cote} {label}.")],
        [md_table(["Champion", "Pick rate", "Winrate (IC 95 %)", "Parties"], rows),
         chart(f, plan.charts, bases, "liste",
               f"Winrate des champions concernés {label}", label)],
        {"lignes": precis, "nombre_de_champions_concernes": len(picked)})

    if params.get("bucket"):
        dims, dim_labels, key = REGION_DIMS, [d["label"] for d in REGION_DIMS], "regions"
        consigne = "Les mêmes champions région par région."
    else:
        dims, dim_labels, key = BUCKET_DIMS, [d["label"] for d in BUCKET_DIMS], "ranks"
        consigne = ("Les mêmes champions bucket par bucket : le constat vaut-il à tous "
                    "les niveaux ? Rappeler que le bucket est celui du joueur échantillonné.")
    s2 = _split_section(f, plan, picked[:8], scope, key, dims, dim_labels, consigne, "")
    s3 = _counts_section(f, scope, min_games, "ensemble",
                         "Remettre ces champions en perspective : combien, parmi tous "
                         "ceux qui atteignent le seuil de parties, sont au-dessus, en "
                         "dessous ou indistinguables de 50 %.", label)
    plan.sections = [s1, s2, s3]
    return plan


# ---------------------------------------------------------------------------
# 3. Bans
# ---------------------------------------------------------------------------

def bans(ds, article) -> StudyPlan:
    params = article.get("params") or {}
    scope, label = scope_of(params), scope_label(params)
    min_games = params.get("min_games", 2000)
    f = Facts(ds)
    f.param("min_games", min_games, f"parties minimum {label}")
    total = ds.matches(scope)
    champs = [c for c in ds.champions() if ds.stats(c, scope)[0] >= min_games]
    if len(champs) < 10:
        raise NotFeasible(f"{len(champs)} champions au-dessus du seuil de parties {label}")
    by_ban = sorted(champs, key=lambda c: -ds.stats(c, scope)[2] / total)
    if ds.stats(by_ban[0], scope)[2] == 0:
        raise NotFeasible("aucun ban enregistré sur cette portée")
    plan = _plan(ds, article, f)

    rows, precis, bases = [], [], []
    for cid in by_ban[:10]:
        base = f.winrate(cid, scope, label)
        br = f.rate(cid, scope, "ban_rate", label)
        verdict = VERDICT[significance(ds.wr(cid, scope))]
        rows.append([ds.names[cid], f.d(br), f.cell(base), f.d(base + ".games"), verdict])
        precis.append(_row_precis(f, cid, [br, *_wr_ids(base)], lecture=verdict))
        bases.append((ds.names[cid], base))
    s1 = Section(
        "plus-bannis",
        f"Les dix champions les plus bannis {label} et leur winrate quand ils sont "
        "joués. Décrire ce que les bans visent, sans prêter d'intention aux joueurs "
        "qui bannissent.",
        [f.key_value(f.rate(by_ban[0], scope, "ban_rate", label),
                     f"Ban rate de {ds.names[by_ban[0]]}, le champion le plus banni {label}.")],
        [md_table(["Champion", "Ban rate", "Winrate (IC 95 %)", "Parties", "Lecture"], rows),
         chart(f, plan.charts, bases, "plus-bannis",
               f"Winrate des dix champions les plus bannis {label}", label)],
        {"lignes": precis,
         "parmi_les_dix_en_dessous_de_50": sum(
             1 for c in by_ban[:10] if significance(ds.wr(c, scope)) == "below"),
         "parmi_les_dix_au_dessus_de_50": sum(
             1 for c in by_ban[:10] if significance(ds.wr(c, scope)) == "above")})

    low = [c for c in champs
           if significance(ds.wr(c, scope)) == "above"
           and ds.stats(c, scope)[2] / total < params.get("max_ban", 0.02)]
    low.sort(key=lambda c: -ds.wr(c, scope)["lo"])
    rows, precis = [], []
    for cid in low[:8]:
        base = f.winrate(cid, scope, label)
        br = f.rate(cid, scope, "ban_rate", label)
        rows.append([ds.names[cid], f.cell(base), f.d(base + ".games"), f.d(br)])
        precis.append(_row_precis(f, cid, [br, *_wr_ids(base)]))
    s2 = Section(
        "peu-bannis",
        "Les champions dont l'intervalle est entièrement au-dessus de 50 % et qui sont "
        "pourtant très peu bannis. S'il n'y en a aucun, l'écrire simplement.",
        [f.key_figure(f.winrate(low[0], scope, label),
                      f"Winrate de {ds.names[low[0]]}, très peu banni {label}.")] if low else [],
        [md_table(["Champion", "Winrate (IC 95 %)", "Parties", "Ban rate"], rows)] if rows else [],
        {"lignes": precis, "nombre": len(low)})

    if params.get("bucket") or params.get("region"):
        dims, dim_labels, key = BUCKET_DIMS, [d["label"] for d in BUCKET_DIMS], "par-rank"
        if params.get("bucket"):
            dims, dim_labels, key = REGION_DIMS, [d["label"] for d in REGION_DIMS], "par-region"
    else:
        dims, dim_labels, key = BUCKET_DIMS, [d["label"] for d in BUCKET_DIMS], "par-rank"
    rows, precis = [], []
    for cid in by_ban[:6]:
        ids = [f.rate(cid, sub(scope, **d["scope"]), "ban_rate", d["label"]) for d in dims]
        rows.append([ds.names[cid], *[f.d(i) for i in ids]])
        precis.append(_row_precis(f, cid, ids))
    s3 = Section(
        key,
        "Le ban rate des champions les plus bannis, découpage par découpage : les "
        "populations ne bannissent pas les mêmes champions dans les mêmes proportions.",
        [], [md_table(["Champion", *dim_labels], rows)], {"lignes": precis})
    plan.sections = [s1, s2, s3]
    return plan


# ---------------------------------------------------------------------------
# 4. Comparaison régionale
# ---------------------------------------------------------------------------

def regions(ds, article) -> StudyPlan:
    params = article.get("params") or {}
    scope, label = scope_of(params), scope_label(params)
    min_games = params.get("min_games", 1200)
    f = Facts(ds)
    f.param("min_region", min_games, f"parties minimum par région {label}")
    base = [c for c in ds.champions()
            if all(ds.stats(c, sub(scope, regions=[r]))[0] >= min_games for r in REGIONS)]
    if len(base) < 10:
        raise NotFeasible(f"{len(base)} champions assez joués dans les trois régions {label}")
    f.add("regions.count.base",
          {"kind": "count", "min_games": min_games, "condition": "any",
           "scopes": [sub(scope, regions=[r]) for r in REGIONS]},
          f"champions joués au moins le seuil dans chacune des trois régions ({label})")
    plan = _plan(ds, article, f)

    def gap(cid):
        s = {r: ds.wr(cid, sub(scope, regions=[r])) for r in REGIONS}
        hi_r = max(REGIONS, key=lambda r: s[r]["wr"])
        lo_r = min(REGIONS, key=lambda r: s[r]["wr"])
        return s[hi_r]["wr"] - s[lo_r]["wr"], hi_r, lo_r, s[hi_r]["lo"] > s[lo_r]["hi"]

    gaps = sorted(base, key=lambda c: -gap(c)[0])
    rows, precis, bases = [], [], []
    for cid in gaps[:8]:
        _, hi_r, lo_r, net = gap(cid)
        cells, ids = [], []
        for r in REGIONS:
            b = f.winrate(cid, sub(scope, regions=[r]), f"{REGION_LABELS[r]}, {label}")
            cells.append(f"{f.cell(b)} ({f.d(b + '.games')})")
            ids += _wr_ids(b)
        d = f.diff(cid, sub(scope, regions=[hi_r]), sub(scope, regions=[lo_r]),
                   f"{REGION_SHORT[hi_r]} moins {REGION_SHORT[lo_r]}")
        ids.append(d)
        rows.append([ds.names[cid], *cells, f.d(d), "oui" if net else "non"])
        precis.append(_row_precis(f, cid, ids, region_haute=REGION_LABELS[hi_r],
                                  region_basse=REGION_LABELS[lo_r], ecart_net=net))
    top = gaps[0]
    _, hi_r, lo_r, _ = gap(top)
    for cid in gaps[:4]:
        _, h, l, _ = gap(cid)
        for r in (h, l):
            bases.append((f"{ds.names[cid]} — {REGION_SHORT[r]}",
                          f.winrate(cid, sub(scope, regions=[r]),
                                    f"{REGION_LABELS[r]}, {label}")))
    kf = f.diff(top, sub(scope, regions=[hi_r]), sub(scope, regions=[lo_r]),
                f"{REGION_SHORT[hi_r]} moins {REGION_SHORT[lo_r]}")
    s1 = Section(
        "ecarts",
        "Les plus grands écarts de winrate d'un même champion entre régions. "
        "« ecart_net » dit si les intervalles des deux régions extrêmes sont disjoints ; "
        "quand il est faux, l'écart est compatible avec du bruit d'échantillonnage et "
        "doit être décrit ainsi.",
        [f.key_value(kf, f"Écart de winrate de {ds.names[top]} entre "
                         f"{REGION_LABELS[hi_r]} et {REGION_LABELS[lo_r]} {label}, le "
                         f"plus grand de la sélection.")],
        [md_table(["Champion", "EUW", "KR", "NA", "Écart max", "Écart net"], rows),
         chart(f, plan.charts, bases, "ecarts-regions",
               f"Winrate par région des champions aux plus grands écarts {label}",
               "intervalle de confiance à 95 %")],
        {"lignes": precis,
         "nombre_ecarts_nets_parmi_ces_lignes": sum(1 for c in gaps[:8] if gap(c)[3])})

    def pick_gap(cid):
        pr = {r: ds.stats(cid, sub(scope, regions=[r]))[0] / ds.matches(sub(scope, regions=[r]))
              for r in REGIONS}
        return max(pr.values()) - min(pr.values()), pr

    picks = sorted(base, key=lambda c: -pick_gap(c)[0])[:8]
    rows, precis = [], []
    for cid in picks:
        ids = [f.rate(cid, sub(scope, regions=[r]), "pick_rate",
                      f"{REGION_LABELS[r]}, {label}") for r in REGIONS]
        pr = pick_gap(cid)[1]
        rows.append([ds.names[cid], *[f.d(i) for i in ids]])
        precis.append(_row_precis(f, cid, ids,
                                  region_la_plus_jouee=REGION_LABELS[max(pr, key=pr.get)],
                                  region_la_moins_jouee=REGION_LABELS[min(pr, key=pr.get)]))
    pr = pick_gap(picks[0])[1]
    s2 = Section(
        "picks",
        "Les champions dont la popularité varie le plus d'une région à l'autre. "
        "Décrire les écarts de choix, sans les expliquer : aucune cause n'est mesurée.",
        [f.key_value(f.rate(picks[0], sub(scope, regions=[max(pr, key=pr.get)]),
                            "pick_rate", REGION_LABELS[max(pr, key=pr.get)]),
                     f"Pick rate de {ds.names[picks[0]]} en "
                     f"{REGION_LABELS[max(pr, key=pr.get)]}, la région où il est le plus "
                     f"joué {label} ; c'est le plus grand écart de popularité de la sélection.")],
        [md_table(["Champion", "Pick rate EUW", "Pick rate KR", "Pick rate NA"], rows)],
        {"lignes": precis})

    ids = {}
    for cond, texte in (("above", "au-dessus de 50 %"), ("below", "en dessous de 50 %")):
        ids[cond] = f.add(f"regions.count.{cond}3",
                          {"kind": "count", "min_games": min_games, "condition": cond,
                           "scopes": [sub(scope, regions=[r]) for r in REGIONS]},
                          f"champions significativement {texte} dans les trois régions ({label})")
    per_region = {
        r: f.add(f"regions.count.above.{REGION_SHORT[r].lower()}",
                 {"kind": "count", "min_games": min_games, "condition": "above",
                  "scopes": [sub(scope, regions=[r])]},
                 f"champions significativement au-dessus de 50 % en {REGION_LABELS[r]} ({label})")
        for r in REGIONS}
    s3 = Section(
        "commun",
        "Ce que les trois régions ont en commun : combien de champions tiennent "
        "au-dessus (ou en dessous) de 50 % partout à la fois, comparé au compte de "
        "chaque région prise seule.",
        [f.key_value(ids["above"], f"Champions au-dessus de 50 % dans les trois régions "
                                   f"à la fois {label}.", unit="champions")],
        [md_table(["Région", "Champions au-dessus de 50 %"],
                  [[REGION_LABELS[r], f.d(per_region[r])] for r in REGIONS])],
        {"faits_globaux": {i: f"{f.d(i)} — {f.items[i]['meaning']}"
                           for i in [*ids.values(), *per_region.values(),
                                     "regions.count.base", "param.min_region"]}})
    plan.sections = [s1, s2, s3]
    plan.extra_limits = ("Chaque région est représentée par une seule plateforme (EUW, "
                         "KR, NA) : les autres serveurs ne sont pas couverts.")
    return plan


# ---------------------------------------------------------------------------
# 5. Écart entre buckets de rank
# ---------------------------------------------------------------------------

def rank_gap(ds, article) -> StudyPlan:
    params = article.get("params") or {}
    scope, label = scope_of(params), scope_label(params)
    min_games = params.get("min_games", 800)
    lo_b = sub(scope, buckets=["IRON_BRONZE"])
    hi_b = sub(scope, buckets=["DIAMOND_PLUS"])
    f = Facts(ds)
    f.param("min_bucket", min_games, f"parties minimum par bucket {label}")
    base = [c for c in ds.champions()
            if ds.stats(c, lo_b)[0] >= min_games and ds.stats(c, hi_b)[0] >= min_games]
    if len(base) < 15:
        raise NotFeasible(f"{len(base)} champions assez joués en Fer–Bronze et en "
                          f"Diamant+ {label}")
    plan = _plan(ds, article, f)
    gap = lambda c: ds.wr(c, hi_b)["wr"] - ds.wr(c, lo_b)["wr"]
    net = lambda c: (ds.wr(c, hi_b)["lo"] > ds.wr(c, lo_b)["hi"]
                     or ds.wr(c, lo_b)["lo"] > ds.wr(c, hi_b)["hi"])

    def gap_section(key, champs, consigne, kf_label, chart_id, chart_title):
        rows, precis, bases = [], [], []
        for cid in champs:
            a = f.winrate(cid, lo_b, f"Fer–Bronze, {label}")
            z = f.winrate(cid, hi_b, f"Diamant+, {label}")
            d = f.diff(cid, hi_b, lo_b, "Diamant+ moins Fer–Bronze")
            rows.append([ds.names[cid], f"{f.cell(a)} ({f.d(a + '.games')})",
                         f"{f.cell(z)} ({f.d(z + '.games')})", f.d(d),
                         "oui" if net(cid) else "non"])
            precis.append(_row_precis(f, cid, [*_wr_ids(a), *_wr_ids(z), d],
                                      ecart_net=net(cid)))
            bases += [(f"{ds.names[cid]} — Fer–Bronze", a), (f"{ds.names[cid]} — Diamant+", z)]
        kf = f.diff(champs[0], hi_b, lo_b, "Diamant+ moins Fer–Bronze")
        return Section(key, consigne, [f.key_value(kf, kf_label.format(name=ds.names[champs[0]]))],
                       [md_table(["Champion", "Fer–Bronze", "Diamant+", "Écart", "Écart net"], rows),
                        chart(f, plan.charts, bases[:8], chart_id, chart_title, label)],
                       {"lignes": precis})

    up = sorted(base, key=lambda c: -gap(c))[:6]
    down = sorted(base, key=gap)[:6]
    s1 = gap_section("montent", up,
                     "Les champions dont le winrate est le plus haut en Diamant+ qu'en "
                     "Fer–Bronze. « ecart_net » dit si les intervalles sont disjoints. "
                     "Rappeler que le bucket est celui du joueur échantillonné.",
                     "Écart de winrate de {name} entre Diamant+ et Fer–Bronze, le plus "
                     "grand de la sélection en faveur du haut de ladder.",
                     "montent", "Champions qui gagnent davantage en Diamant+")
    s2 = gap_section("descendent", down,
                     "Les champions dont le winrate est le plus haut en Fer–Bronze qu'en "
                     "Diamant+. Ne jamais écrire qu'un champion est fort ou faible sans "
                     "préciser le niveau de jeu.",
                     "Écart de winrate de {name} entre Diamant+ et Fer–Bronze, le plus "
                     "grand de la sélection en faveur du bas de ladder.",
                     "descendent", "Champions qui gagnent davantage en Fer–Bronze")

    stable_scopes = [sub(scope, buckets=[b]) for b in BUCKETS]
    f.param("min_stable", min_games, "parties minimum par bucket pour la stabilité")
    n_stable = f.add("rank.count.stable",
                     {"kind": "count", "min_games": min_games, "condition": "above",
                      "scopes": stable_scopes},
                     f"champions au-dessus de 50 % dans les quatre buckets ({label})")
    stable = sorted([c for c in ds.champions()
                     if all(ds.stats(c, s)[0] >= min_games
                            and significance(ds.wr(c, s)) == "above" for s in stable_scopes)],
                    key=lambda c: -min(ds.wr(c, s)["lo"] for s in stable_scopes))
    rows, precis = [], []
    for cid in stable[:6]:
        cells, ids = [], []
        for s, b in zip(stable_scopes, BUCKETS):
            base_id = f.winrate(cid, s, f"{BUCKET_LABELS[b]}, {label}")
            cells.append(f.d(base_id + ".wr"))
            ids += _wr_ids(base_id)
        rows.append([ds.names[cid], *cells])
        precis.append(_row_precis(f, cid, ids))
    s3 = Section(
        "stables",
        "Les champions au-dessus de 50 % dans les quatre buckets à la fois : ceux dont "
        "le résultat ne dépend pas du niveau de jeu. S'il n'y en a aucun, l'écrire.",
        [f.key_value(n_stable, f"Champions au-dessus de 50 % dans les quatre buckets "
                               f"{label}.", unit="champions")],
        [md_table(["Champion", *[BUCKET_LABELS[b] for b in BUCKETS]], rows)] if rows else [],
        {"lignes": precis,
         "faits_globaux": {n_stable: f"{f.d(n_stable)} — {f.items[n_stable]['meaning']}"}})
    plan.sections = [s1, s2, s3]
    return plan


# ---------------------------------------------------------------------------
# 6. Classement sur une métrique du Lot 13
# ---------------------------------------------------------------------------

def metric_ranking(ds, article) -> StudyPlan:
    params = article.get("params") or {}
    metric = params["metric"]
    scope, label = scope_of(params), scope_label(params)
    min_n = params.get("min_n", 800)
    mlabel, _, _, _, unit = METRICS[metric]
    f = Facts(ds)
    f.param("min_n", min_n, f"participations minimum {label}")
    champs = [c for c in ds.champions() if ds.metric_stats(c, metric, scope)[0] >= min_n]
    if len(champs) < 10:
        raise NotFeasible(f"{len(champs)} champions avec {min_n} participations mesurées "
                          f"pour {mlabel} {label}")
    mean = lambda c: ds.metric_stats(c, metric, scope)[1] / ds.metric_stats(c, metric, scope)[0]
    champs.sort(key=lambda c: -mean(c))
    plan = _plan(ds, article, f)

    def metric_table(selection, key, consigne, kf_label):
        rows, precis = [], []
        for cid in selection:
            base = f.metric(cid, metric, scope, label)
            wr = f.winrate(cid, scope, label)
            rows.append([ds.names[cid], f.mean_cell(base), f.d(base + ".mean_n"),
                         f.cell(wr)])
            precis.append(_row_precis(
                f, cid,
                [f"{base}.mean", f"{base}.mean_lo", f"{base}.mean_hi", f"{base}.mean_n",
                 *_wr_ids(wr)],
                lecture_du_winrate=VERDICT[significance(ds.wr(cid, scope))]))
        return Section(
            key, consigne,
            [f.mean_key_figure(f.metric(selection[0], metric, scope, label),
                               kf_label.format(name=ds.names[selection[0]]))],
            [md_table(["Champion", f"{mlabel.capitalize()} moyen (IC 95 %)",
                       "Participations", "Winrate (IC 95 %)"], rows)],
            {"lignes": precis})

    s1 = metric_table(
        champs[:8], "hauts",
        f"Les champions au {mlabel} moyen le plus élevé {label}. La moyenne est donnée "
        f"avec son intervalle de confiance à 95 % ; le winrate est rappelé à côté, sans "
        "affirmer de lien entre les deux : rien ici ne mesure une cause.",
        "{name} affiche la moyenne la plus élevée de la sélection.")
    s2 = metric_table(
        champs[-8:][::-1], "bas",
        f"À l'autre bout du classement, les champions au {mlabel} moyen le plus bas "
        f"{label}. Décrire, ne pas conseiller.",
        "{name} affiche la moyenne la plus basse de la sélection.")

    if params.get("bucket"):
        dims, dim_labels, key = REGION_DIMS, [d["label"] for d in REGION_DIMS], "regions"
        consigne = f"Le {mlabel} moyen des mêmes champions, région par région."
    else:
        dims, dim_labels, key = BUCKET_DIMS, [d["label"] for d in BUCKET_DIMS], "ranks"
        consigne = (f"Le {mlabel} moyen des mêmes champions, bucket par bucket. "
                    "Rappeler que le bucket est celui du joueur échantillonné.")
    rows, precis = [], []
    for cid in champs[:6]:
        cells, ids = [], []
        for dim in dims:
            s = sub(scope, **dim["scope"])
            if ds.metric_stats(cid, metric, s)[0] < max(100, min_n // 6):
                cells.append("échantillon insuffisant")
                continue
            base = f.metric(cid, metric, s, f"{dim['label']}, {label}")
            cells.append(f.d(base + ".mean"))
            ids += [f"{base}.mean", f"{base}.mean_lo", f"{base}.mean_hi", f"{base}.mean_n"]
        if not ids:
            continue
        rows.append([ds.names[cid], *cells])
        precis.append(_row_precis(f, cid, ids))
    if not rows:
        raise NotFeasible("aucun champion mesurable sur tous les découpages")
    s3 = Section(key, consigne, [], [md_table(["Champion", *dim_labels], rows)],
                 {"lignes": precis})
    plan.sections = [s1, s2, s3]
    plan.extra_limits = (f"Le {mlabel} dépend aussi de la durée des parties et du rôle "
                         "joué : les moyennes se comparent à l'intérieur d'une même "
                         "sélection, pas d'une étude à l'autre.")
    return plan


# ---------------------------------------------------------------------------
# 7. Taux issu des timelines (morts avant 5 minutes)
# ---------------------------------------------------------------------------

def timeline_rate(ds, article) -> StudyPlan:
    params = article.get("params") or {}
    event = params.get("event", "morts_avant_5min")
    scope, label = scope_of(params), scope_label(params)
    min_n = params.get("min_n", 400)
    f = Facts(ds)
    f.param("min_n", min_n, f"parties avec timeline minimum {label}")
    champs = [c for c in ds.champions() if ds.rate_stats(c, event, scope)[0] >= min_n]
    if len(champs) < 10:
        raise NotFeasible(f"{len(champs)} champions avec {min_n} parties de timeline {label}")
    rate = lambda c: (ds.rate_stats(c, event, scope)[1] / ds.rate_stats(c, event, scope)[0])
    champs.sort(key=lambda c: -rate(c))
    plan = _plan(ds, article, f)
    quoi = TL_EVENTS[event][1]

    def rate_table(selection, key, consigne, kf_label):
        rows, precis = [], []
        for cid in selection:
            base = f.event_rate(cid, event, scope, label)
            wr = f.winrate(cid, scope, label)
            rows.append([ds.names[cid], f.rate_cell(base), f.d(base + ".rate_n"), f.cell(wr)])
            precis.append(_row_precis(
                f, cid,
                [f"{base}.rate", f"{base}.rate_lo", f"{base}.rate_hi", f"{base}.rate_n",
                 *_wr_ids(wr)],
                lecture_du_winrate=VERDICT[significance(ds.wr(cid, scope))]))
        return Section(
            key, consigne,
            [f.rate_key_figure(f.event_rate(selection[0], event, scope, label),
                               kf_label.format(name=ds.names[selection[0]]))],
            [md_table(["Champion", "Part des parties (IC 95 %)", "Parties avec timeline",
                       "Winrate (IC 95 %)"], rows)],
            {"lignes": precis})

    s1 = rate_table(champs[:8], "hauts",
                    f"Les champions pour lesquels la {quoi} est la plus fréquente {label}. "
                    "Le taux porte son intervalle de confiance ; le winrate est rappelé "
                    "à côté, sans affirmer de lien.",
                    "{name} est le champion le plus concerné de la sélection.")
    s2 = rate_table(champs[-8:][::-1], "bas",
                    f"Les champions les moins concernés {label}.",
                    "{name} est le champion le moins concerné de la sélection.")
    rows, precis = [], []
    for cid in champs[:6]:
        cells, ids = [], []
        for dim in BUCKET_DIMS:
            s = sub(scope, **dim["scope"])
            if ds.rate_stats(cid, event, s)[0] < max(100, min_n // 6):
                cells.append("échantillon insuffisant")
                continue
            base = f.event_rate(cid, event, s, f"{dim['label']}, {label}")
            cells.append(f.d(base + ".rate"))
            ids += [f"{base}.rate", f"{base}.rate_lo", f"{base}.rate_hi", f"{base}.rate_n"]
        if not ids:
            continue
        rows.append([ds.names[cid], *cells])
        precis.append(_row_precis(f, cid, ids))
    if not rows:
        raise NotFeasible("aucun champion mesurable bucket par bucket")
    s3 = Section("ranks",
                 f"La même mesure bucket par bucket. Rappeler que le bucket est celui du "
                 "joueur échantillonné.",
                 [], [md_table(["Champion", *[d["label"] for d in BUCKET_DIMS]], rows)],
                 {"lignes": precis})
    plan.sections = [s1, s2, s3]
    plan.extra_limits = ("Les timelines ne sont collectées que sur un échantillon des "
                         "parties (environ une sur dix) : les effectifs de cette étude "
                         "sont ceux de cet échantillon, pas de toutes les parties du patch.")
    return plan


BUILDERS = {
    "wr-ranking": wr_ranking,
    "wr-outliers": wr_outliers,
    "bans": bans,
    "regions": regions,
    "rank-gap": rank_gap,
    "metric-ranking": metric_ranking,
    "timeline-rate": timeline_rate,
}
