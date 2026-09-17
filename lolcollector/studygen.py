"""Génération d'études EloLab : données, faits, sujets et rendu MDX.

Tout chiffre d'une étude générée est un « fait » : une valeur calculée ici,
à partir des mêmes agrégats que `collector.py export`, et enregistrée avec
la formule qui la produit (`spec`). Le rédacteur (claude -p) ne voit que le
précis et ne cite les chiffres que par leur identifiant `{{id}}` ; c'est ce
module qui les remplace par leur valeur formatée. `scripts/verify_generated.py`
recalcule ensuite chaque fait depuis les JSON exportés, indépendamment de ce
code.

Aucune écriture en base : `open_readonly` ouvre matches.db en mode=ro, ce qui
ne bloque jamais le collecteur (WAL).
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field

from .export import (
    MIN_REGION_MATCHES, ROLES, compute_tierlist, fetch_champion_names,
    patch_to_slug, wilson_ci,
)

REGIONS = ("europe", "asia", "americas")
BUCKETS = ("IRON_BRONZE", "SILVER_GOLD", "PLAT_EMERALD", "DIAMOND_PLUS")
REGION_LABELS = {"europe": "Europe (EUW)", "asia": "Asie (KR)", "americas": "Amériques (NA)"}
REGION_SHORT = {"europe": "EUW", "asia": "KR", "americas": "NA"}
BUCKET_LABELS = {
    "IRON_BRONZE": "Fer–Bronze", "SILVER_GOLD": "Argent–Or",
    "PLAT_EMERALD": "Platine–Émeraude", "DIAMOND_PLUS": "Diamant+",
}
ROLE_LABELS = {"TOP": "Top", "JUNGLE": "Jungle", "MIDDLE": "Mid",
               "BOTTOM": "Bot (ADC)", "UTILITY": "Support"}
MIN_CELL_GAMES = 200


# ---------------------------------------------------------------------------
# Lecture de la base, sans écriture
# ---------------------------------------------------------------------------

def open_readonly(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"base introuvable : {db_path}")
    uri = "file:" + os.path.abspath(db_path).replace("\\", "/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.execute("PRAGMA query_only = 1")
    return conn


def current_patch(conn: sqlite3.Connection) -> str | None:
    """Patch courant EN BASE : celui du dernier match inséré."""
    row = conn.execute(
        "SELECT patch FROM matches ORDER BY inserted_at DESC LIMIT 1").fetchone()
    return row[0] if row else None


def coverage(conn: sqlite3.Connection, patch: str) -> dict:
    per_region = {r: 0 for r in REGIONS}
    for region, count in conn.execute(
            "SELECT region, COUNT(*) FROM matches WHERE patch = ? GROUP BY region",
            (patch,)):
        per_region[region] = count
    first, last = conn.execute(
        "SELECT MIN(inserted_at), MAX(inserted_at) FROM matches WHERE patch = ?",
        (patch,)).fetchone()
    return {"patch": patch, "per_region": per_region,
            "total": sum(per_region.values()), "first": first, "last": last}


def check_gates(cov: dict, min_region: int = MIN_REGION_MATCHES,
                min_age_days: float = 3, min_total: int = 50_000,
                now: float | None = None) -> list[str]:
    """Raisons de refus ; liste vide = les gates passent.

    - couverture : chaque région analysée a au moins `min_region` matchs ;
    - maturité (Lot 3) : patch collecté depuis `min_age_days` jours et
      `min_total` matchs au total.
    """
    now = time.time() if now is None else now
    reasons = []
    for region in REGIONS:
        n = cov["per_region"].get(region, 0)
        if n < min_region:
            reasons.append(f"couverture : {region} n'a que {n} matchs sur le patch "
                           f"{cov['patch']} (minimum {min_region})")
    if cov["total"] < min_total:
        reasons.append(f"maturité : {cov['total']} matchs au total (minimum {min_total})")
    if cov["first"] is None or (now - cov["first"]) / 86400 < min_age_days:
        age = 0 if cov["first"] is None else (now - cov["first"]) / 86400
        reasons.append(f"maturité : patch collecté depuis {age:.1f} jours "
                       f"(minimum {min_age_days})")
    return reasons


# ---------------------------------------------------------------------------
# Jeu de données : les mêmes agrégats que l'export
# ---------------------------------------------------------------------------

@dataclass
class Dataset:
    patch: str
    rows: list
    role_rows: list
    cells: dict
    first: int | None
    last: int | None
    names: dict = field(default_factory=dict)

    def __post_init__(self):
        self.by_champ: dict[int, list] = {}
        for r in self.rows:
            self.by_champ.setdefault(r["champion_id"], []).append(r)
            self.names[r["champion_id"]] = r["champion_name"]
        self.role_by_champ: dict[int, list] = {}
        for r in self.role_rows:
            self.role_by_champ.setdefault(r["champion_id"], []).append(r)

    @classmethod
    def load(cls, conn, patch, ddragon_version=None):
        rows, role_rows, cells, first, last = compute_tierlist(
            conn, patch, ddragon_version, MIN_CELL_GAMES)
        return cls(patch, rows, role_rows, cells, first, last)

    @property
    def total_matches(self) -> int:
        return sum(self.cells.values())

    @staticmethod
    def _in_scope(row, scope):
        regions, buckets = scope.get("regions"), scope.get("buckets")
        return ((not regions or row["region"] in regions)
                and (not buckets or row["bucket"] in buckets))

    def matches(self, scope) -> int:
        return sum(n for (region, bucket), n in self.cells.items()
                   if self._in_scope({"region": region, "bucket": bucket}, scope))

    def stats(self, cid, scope) -> tuple[int, int, int]:
        """(games, wins, bans) d'un champion ; games/wins au poste si role."""
        bans = sum(r["bans"] for r in self.by_champ.get(cid, [])
                   if self._in_scope(r, scope))
        source = (self.role_by_champ.get(cid, []) if scope.get("role")
                  else self.by_champ.get(cid, []))
        games = wins = 0
        for r in source:
            if scope.get("role") and r["role"] != scope["role"]:
                continue
            if self._in_scope(r, scope):
                games += r["games"]
                wins += r["wins"]
        return games, wins, bans

    def champions(self) -> list[int]:
        return sorted(self.by_champ)

    # accès pratiques pour la sélection des sujets
    def wr(self, cid, scope):
        g, w, _ = self.stats(cid, scope)
        if not g:
            return None
        lo, hi = wilson_ci(w, g)
        return {"games": g, "wins": w, "wr": w / g, "lo": lo, "hi": hi}

    def export_meta(self, ddragon_version=None) -> dict:
        usable = sum(1 for r in self.rows if not r["insufficient_sample"])
        fmt = lambda ts: time.strftime("%Y-%m-%d", time.gmtime(ts)) if ts else None
        return {
            "study": "tierlist", "patch": self.patch,
            "ddragon_version": ddragon_version,
            "exported_at": time.strftime("%Y-%m-%d", time.gmtime()),
            "collected_from": fmt(self.first), "collected_to": fmt(self.last),
            "total_matches": self.total_matches,
            "regions": sorted({region for region, _ in self.cells}),
            "roles": list(ROLES), "role_cells": len(self.role_rows),
            "min_cell_games": MIN_CELL_GAMES,
            "min_region_matches": MIN_REGION_MATCHES,
            "total_cells": len(self.rows), "usable_cells": usable,
            "cells": [{"region": region, "bucket": bucket, "matches": n}
                      for (region, bucket), n in sorted(self.cells.items())],
        }


def significance(s) -> str:
    if s["lo"] > 0.5:
        return "above"
    if s["hi"] < 0.5:
        return "below"
    return "contains"


VERDICT = {"above": "au-dessus de 50 %", "below": "en dessous de 50 %",
           "contains": "indistinguable de 50 %"}


# ---------------------------------------------------------------------------
# Faits
# ---------------------------------------------------------------------------

def fr_int(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def fr_dec(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


PERCENT_KINDS = {"winrate", "ci_low", "ci_high", "pick_rate", "ban_rate"}
INT_KINDS = {"games", "wins", "bans", "matches", "count", "param"}


def format_fact(kind: str, value) -> str:
    if kind in ("winrate", "pick_rate", "ban_rate"):
        return fr_dec(value * 100) + " %"
    if kind in ("ci_low", "ci_high"):
        return fr_dec(value * 100)
    if kind == "wr_diff":
        sign = "+" if value >= 0 else "−"
        return sign + fr_dec(abs(value) * 100) + " pts"
    return fr_int(value)


def evaluate(ds: Dataset, spec: dict):
    kind = spec["kind"]
    scope = spec.get("scope", {})
    if kind == "param":
        return spec["value"]
    if kind == "matches":
        return ds.matches(scope)
    if kind == "count":
        n = 0
        for cid in ds.champions():
            ok = True
            for sc in spec["scopes"]:
                g, w, _ = ds.stats(cid, sc)
                if g < spec["min_games"]:
                    ok = False
                    break
                lo, hi = wilson_ci(w, g)
                cond = spec["condition"]
                if ((cond == "above" and not lo > 0.5)
                        or (cond == "below" and not hi < 0.5)
                        or (cond == "contains" and not (lo <= 0.5 <= hi))):
                    ok = False
                    break
            n += ok
        return n
    if kind == "wr_diff":
        a = evaluate(ds, {"kind": "winrate", "champion_id": spec["champion_id"],
                          "scope": spec["scope"]})
        b = evaluate(ds, {"kind": "winrate", "champion_id": spec["champion_id"],
                          "scope": spec["minus"]})
        return a - b
    games, wins, bans = ds.stats(spec["champion_id"], scope)
    if kind == "games":
        return games
    if kind == "wins":
        return wins
    if kind == "bans":
        return bans
    if kind == "winrate":
        return wins / games
    if kind in ("ci_low", "ci_high"):
        lo, hi = wilson_ci(wins, games)
        return lo if kind == "ci_low" else hi
    if kind == "pick_rate":
        return games / ds.matches(scope)
    if kind == "ban_rate":
        return bans / ds.matches(scope)
    raise ValueError(f"type de fait inconnu : {kind}")


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", text.lower()) or "x"


def scope_key(scope: dict) -> str:
    parts = []
    if scope.get("role"):
        parts.append(scope["role"].lower())
    for r in scope.get("regions") or []:
        parts.append(REGION_SHORT[r].lower())
    for b in scope.get("buckets") or []:
        parts.append(slugify(BUCKET_LABELS[b]))
    return "-".join(parts) or "global"


class Facts:
    def __init__(self, ds: Dataset):
        self.ds = ds
        self.items: dict[str, dict] = {}

    def add(self, fid: str, spec: dict, meaning: str) -> str:
        if fid not in self.items:
            value = evaluate(self.ds, spec)
            self.items[fid] = {"spec": spec, "value": value,
                               "display": format_fact(spec["kind"], value),
                               "champion_id": spec.get("champion_id"),
                               "meaning": meaning}
        return fid

    def param(self, name: str, value: int, meaning: str) -> str:
        return self.add(f"param.{name}", {"kind": "param", "value": value}, meaning)

    def champ_id(self, cid: int) -> str:
        return slugify(self.ds.names[cid])

    def winrate(self, cid: int, scope: dict, label: str) -> str:
        """Winrate + IC + parties d'un champion ; rend l'id de base."""
        base = f"{self.champ_id(cid)}.{scope_key(scope)}"
        name = self.ds.names[cid]
        common = {"champion_id": cid, "scope": scope}
        self.add(f"{base}.wr", {"kind": "winrate", **common}, f"winrate de {name} ({label})")
        self.add(f"{base}.wr_lo", {"kind": "ci_low", **common},
                 f"borne basse de l'IC à 95 % du winrate de {name} ({label})")
        self.add(f"{base}.wr_hi", {"kind": "ci_high", **common},
                 f"borne haute de l'IC à 95 % du winrate de {name} ({label})")
        self.add(f"{base}.games", {"kind": "games", **common},
                 f"parties de {name} ({label})")
        return base

    def rate(self, cid: int, scope: dict, kind: str, label: str) -> str:
        what = {"pick_rate": "pick rate", "ban_rate": "ban rate", "bans": "bans"}[kind]
        fid = f"{self.champ_id(cid)}.{scope_key(scope)}.{kind}"
        return self.add(fid, {"kind": kind, "champion_id": cid, "scope": scope},
                        f"{what} de {self.ds.names[cid]} ({label})")

    def diff(self, cid: int, scope: dict, minus: dict, label: str) -> str:
        fid = f"{self.champ_id(cid)}.{scope_key(scope)}.vs.{scope_key(minus)}"
        return self.add(fid, {"kind": "wr_diff", "champion_id": cid, "scope": scope,
                              "minus": minus},
                        f"écart de winrate de {self.ds.names[cid]} : {label} (en points)")

    # rendu
    def d(self, fid: str) -> str:
        return self.items[fid]["display"]

    def stat(self, base: str) -> str:
        return (f'<Stat value="{self.d(base + ".wr").removesuffix(" %")}" '
                f'ci="{self.d(base + ".wr_lo")} – {self.d(base + ".wr_hi")}" />')

    def cell(self, base: str) -> str:
        return (f'{self.d(base + ".wr")} `[{self.d(base + ".wr_lo")} – '
                f'{self.d(base + ".wr_hi")}]`')

    def key_figure(self, base: str, label: str) -> str:
        return (f'<KeyFigure value="{self.d(base + ".wr").removesuffix(" %")}" '
                f'ci="{self.d(base + ".wr_lo")} – {self.d(base + ".wr_hi")}" '
                f'label="{label}" sample="{self.d(base + ".games")} parties" />')

    def key_value(self, fid: str, label: str, unit: str = "%") -> str:
        value = self.d(fid).removesuffix(" %").removesuffix(" pts")
        unit = "pts" if self.items[fid]["spec"]["kind"] == "wr_diff" else unit
        return f'<KeyFigure value="{value}" unit="{unit}" label="{label}" />'


def md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Plans d'étude
# ---------------------------------------------------------------------------

@dataclass
class Section:
    key: str
    consigne: str
    before: list[str]          # blocs MDX avant la prose (KeyFigure)
    after: list[str]           # blocs MDX après la prose (tableau, graphique)
    precis: dict
    titre: str | None = None   # imposé (étude sans prose)
    texte: str | None = None


@dataclass
class StudyPlan:
    topic: str
    family: str
    title: str
    angle: str
    tags: list[str]
    facts: Facts
    sections: list[Section]
    charts: dict = field(default_factory=dict)
    prose: bool = True
    description: str | None = None
    extra_limits: str = ""

    @property
    def slug(self) -> str:
        return patch_to_slug(self.facts.ds.patch)


class NotFeasible(Exception):
    """Les données du patch ne permettent pas ce sujet (trop peu de cas)."""


GLOBAL = {}


def _row_precis(f: Facts, cid: int, ids: list[str], **flags) -> dict:
    return {"champion": f.ds.names[cid],
            "faits": {i: f"{f.d(i)} — {f.items[i]['meaning']}" for i in ids},
            **flags}


def _wr_ids(base: str) -> list[str]:
    return [f"{base}.wr", f"{base}.wr_lo", f"{base}.wr_hi", f"{base}.games"]


def chart(f: Facts, plan_charts: dict, cid_bases: list[tuple[str, str]],
          chart_id: str, title: str, subtitle: str) -> str:
    plan_charts[chart_id] = {
        "title": title, "subtitle": subtitle,
        "rows": [{"label": label, "wr": f"{base}.wr", "lo": f"{base}.wr_lo",
                  "hi": f"{base}.wr_hi", "games": f"{base}.games"}
                 for label, base in cid_bases],
    }
    return f'<IntervalChart id="{chart_id}" />'


def _intro_example(f: Facts, scope: dict, min_games: int) -> dict:
    """Deux champions pour la phrase de lecture des intervalles."""
    ds = f.ds
    above = contains = None
    for cid in sorted(ds.champions(), key=lambda c: -ds.stats(c, scope)[0]):
        s = ds.wr(cid, scope)
        if not s or s["games"] < min_games:
            continue
        if above is None and significance(s) == "above":
            above = cid
        if contains is None and significance(s) == "contains":
            contains = cid
        if above and contains:
            break
    out = {}
    if above:
        out["above"] = (above, f.winrate(above, scope, "toutes régions, tous ranks"))
    if contains:
        out["contains"] = (contains, f.winrate(contains, scope, "toutes régions, tous ranks"))
    return out


def plan_tierlist(ds: Dataset) -> StudyPlan:
    """Tier list du patch : purement données, textes fixes, aucun LLM."""
    f = Facts(ds)
    patch = ds.patch
    vol = 5000
    f.param("volume", vol, "seuil de volume")
    total = f.add("global.matches", {"kind": "matches", "scope": GLOBAL}, "matchs du patch")
    counted = f.param("min_champ", 1000, "parties minimum pour être compté")
    n_above = f.add("global.count.above", {"kind": "count", "scopes": [GLOBAL],
                    "min_games": 1000, "condition": "above"}, "champions au-dessus de 50 %")
    n_below = f.add("global.count.below", {"kind": "count", "scopes": [GLOBAL],
                    "min_games": 1000, "condition": "below"}, "champions en dessous de 50 %")
    n_contains = f.add("global.count.contains", {"kind": "count", "scopes": [GLOBAL],
                       "min_games": 1000, "condition": "contains"},
                       "champions indistinguables de 50 %")

    by_pick = sorted(ds.champions(), key=lambda c: -ds.stats(c, GLOBAL)[0])
    top_pick = by_pick[:10]
    rows = []
    for cid in top_pick:
        b = f.winrate(cid, GLOBAL, "toutes régions, tous ranks")
        p = f.rate(cid, GLOBAL, "pick_rate", "toutes régions, tous ranks")
        rows.append([ds.names[cid], f.d(p), f.cell(b), f.d(b + ".games"),
                     VERDICT[significance(ds.wr(cid, GLOBAL))]])
    table_pick = md_table(["Champion", "Pick rate", "Winrate (IC 95 %)", "Parties", "Lecture"], rows)

    strong = [c for c in ds.champions() if ds.stats(c, GLOBAL)[0] >= vol]
    strong.sort(key=lambda c: -ds.wr(c, GLOBAL)["lo"])
    if len(strong) < 6:
        raise NotFeasible("moins de 6 champions au-dessus du seuil de volume")
    rows = []
    bases = []
    for cid in strong[:10]:
        b = f.winrate(cid, GLOBAL, "toutes régions, tous ranks")
        p = f.rate(cid, GLOBAL, "pick_rate", "toutes régions, tous ranks")
        br = f.rate(cid, GLOBAL, "ban_rate", "toutes régions, tous ranks")
        rows.append([ds.names[cid], f.cell(b), f.d(b + ".games"), f.d(p), f.d(br)])
        bases.append((ds.names[cid], b))
    table_wr = md_table(["Champion", "Winrate (IC 95 %)", "Parties", "Pick rate", "Ban rate"], rows)
    plan = StudyPlan("tierlist-globale", "tierlist", f"Tier list — patch {patch}",
                     "", ["Tier list", "Tous rôles", "Tous ranks", "EUW · KR · NA"],
                     f, [], prose=False)
    ch = chart(f, plan.charts, bases, "meilleurs-winrates",
               "Meilleurs winrates du patch, avec leur intervalle de confiance",
               "toutes régions, tous ranks")

    top = top_pick[0]
    top_b = f.winrate(top, GLOBAL, "toutes régions, tous ranks")
    best = strong[0]
    best_b = f.winrate(best, GLOBAL, "toutes régions, tous ranks")
    plan.description = (
        f"Winrate, pick et ban par champion, rank et région sur {f.d(total)} matchs "
        f"ranked solo du patch {patch}, chaque winrate avec son intervalle de confiance à 95 %.")
    plan.sections = [
        Section("comptes", "", [], [], {}, titre="Ce que disent les intervalles",
                texte=(f"Sur les champions joués au moins {f.d(counted)} fois, "
                       f"**{f.d(n_above)} sont au-dessus de 50 %**, {f.d(n_below)} sont en "
                       f"dessous et {f.d(n_contains)} restent indistinguables de 50 % : "
                       "leur intervalle de confiance contient encore l'équilibre.")),
        Section("pick", "", [f.key_figure(top_b, f"Winrate de {ds.names[top]}, champion le plus joué du patch.")],
                [table_pick], {}, titre="Les champions les plus joués",
                texte="Les dix champions les plus présents du patch, avec leur winrate et "
                      "la lecture de leur intervalle de confiance."),
        Section("winrate", "", [f.key_figure(best_b, f"Winrate de {ds.names[best]}, meilleure borne basse d'intervalle du patch parmi les champions à au moins {f.d('param.volume')} parties.")],
                [table_wr, ch], {}, titre="Les meilleurs winrates à gros volume",
                texte=(f"Parmi les champions joués au moins {f.d('param.volume')} fois, classés "
                       "par la borne basse de leur intervalle de confiance : c'est la valeur "
                       "que l'échantillon garantit, pas la plus flatteuse.")),
    ]
    return plan


def plan_regions(ds: Dataset) -> StudyPlan:
    f = Facts(ds)
    patch = ds.patch
    min_g = 2000
    f.param("min_region", min_g, "parties minimum par région")
    base = [c for c in ds.champions()
            if all(ds.stats(c, {"regions": [r]})[0] >= min_g for r in REGIONS)]
    if len(base) < 10:
        raise NotFeasible("moins de 10 champions assez joués dans les trois régions")
    f.add("regions.count.base", {"kind": "count", "min_games": min_g, "condition": "any",
          "scopes": [{"regions": [r]} for r in REGIONS]},
          "champions joués au moins le seuil dans chacune des trois régions")

    def gap(cid):
        s = {r: ds.wr(cid, {"regions": [r]}) for r in REGIONS}
        hi_r = max(REGIONS, key=lambda r: s[r]["wr"])
        lo_r = min(REGIONS, key=lambda r: s[r]["wr"])
        net = s[hi_r]["lo"] > s[lo_r]["hi"]
        return s[hi_r]["wr"] - s[lo_r]["wr"], hi_r, lo_r, net

    gaps = sorted(base, key=lambda c: -gap(c)[0])

    plan = StudyPlan("comparaison-regionale", "regions",
                     f"EUW, KR, NA : trois métas ? Patch {patch}",
                     "Mesurer l'écart réel entre les trois régions et distinguer les vraies "
                     "différences de méta du bruit d'échantillonnage.",
                     ["Régions", "Tous ranks", "EUW · KR · NA"], f, [])

    # Section 1 : les plus grands écarts de winrate
    rows, precis_rows, bases = [], [], []
    for cid in gaps[:8]:
        g, hi_r, lo_r, is_net = gap(cid)
        cells = []
        ids = []
        for r in REGIONS:
            b = f.winrate(cid, {"regions": [r]}, REGION_LABELS[r])
            cells.append(f"{f.cell(b)} ({f.d(b + '.games')})")
            ids += _wr_ids(b)
        dfid = f.diff(cid, {"regions": [hi_r]}, {"regions": [lo_r]},
                      f"{REGION_SHORT[hi_r]} moins {REGION_SHORT[lo_r]}")
        ids.append(dfid)
        rows.append([ds.names[cid], *cells, f.d(dfid), "oui" if is_net else "non"])
        precis_rows.append(_row_precis(f, cid, ids, region_haute=REGION_LABELS[hi_r],
                                       region_basse=REGION_LABELS[lo_r],
                                       ecart_net=is_net))
    top = gaps[0]
    g, hi_r, lo_r, _ = gap(top)
    kf_id = f.diff(top, {"regions": [hi_r]}, {"regions": [lo_r]},
                   f"{REGION_SHORT[hi_r]} moins {REGION_SHORT[lo_r]}")
    for r in (hi_r, lo_r):
        bases.append((f"{ds.names[top]} — {REGION_SHORT[r]}",
                      f.winrate(top, {"regions": [r]}, REGION_LABELS[r])))
    for cid in gaps[1:4]:
        _, h, l, _ = gap(cid)
        for r in (h, l):
            bases.append((f"{ds.names[cid]} — {REGION_SHORT[r]}",
                          f.winrate(cid, {"regions": [r]}, REGION_LABELS[r])))
    s1 = Section(
        "ecarts",
        "Les plus grands écarts de winrate d'un même champion entre régions. La colonne "
        "« Écart net » (ecart_net) dit si les intervalles des deux régions extrêmes sont "
        "disjoints ; si ecart_net est faux, l'écart peut être du bruit et doit être décrit ainsi.",
        [f.key_value(kf_id, f"Écart de winrate de {ds.names[top]} entre {REGION_LABELS[hi_r]} et {REGION_LABELS[lo_r]}, le plus grand du patch parmi les champions à au moins {f.d('param.min_region')} parties par région.")],
        [md_table(["Champion", "EUW", "KR", "NA", "Écart max", "Écart net"], rows),
         chart(f, plan.charts, bases, "ecarts-regions",
               "Winrate par région des champions aux plus grands écarts",
               "intervalle de confiance à 95 %, tous ranks")],
        {"lignes": precis_rows,
         "nombre_ecarts_nets_parmi_ces_lignes": sum(1 for c in gaps[:8] if gap(c)[3])})

    # Section 2 : les picks régionaux
    def pick_gap(cid):
        pr = {r: ds.stats(cid, {"regions": [r]})[0] / ds.matches({"regions": [r]})
              for r in REGIONS}
        return max(pr.values()) - min(pr.values()), pr

    picks = sorted(base, key=lambda c: -pick_gap(c)[0])[:8]
    rows, precis_rows = [], []
    for cid in picks:
        ids = [f.rate(cid, {"regions": [r]}, "pick_rate", REGION_LABELS[r]) for r in REGIONS]
        rows.append([ds.names[cid], *[f.d(i) for i in ids]])
        pr = pick_gap(cid)[1]
        precis_rows.append(_row_precis(f, cid, ids,
                                       region_la_plus_jouee=REGION_LABELS[max(pr, key=pr.get)],
                                       region_la_moins_jouee=REGION_LABELS[min(pr, key=pr.get)]))
    top_p = picks[0]
    pr = pick_gap(top_p)[1]
    kf_p = f.rate(top_p, {"regions": [max(pr, key=pr.get)]}, "pick_rate",
                  REGION_LABELS[max(pr, key=pr.get)])
    s2 = Section(
        "picks",
        "Les champions dont la popularité (pick rate) varie le plus d'une région à l'autre. "
        "Décrire les écarts de choix, sans les expliquer : aucune cause n'est mesurée.",
        [f.key_value(kf_p, f"Pick rate de {ds.names[top_p]} en {REGION_LABELS[max(pr, key=pr.get)]}, la région où il est le plus joué ; c'est le plus grand écart de popularité entre régions du patch.")],
        [md_table(["Champion", "Pick rate EUW", "Pick rate KR", "Pick rate NA"], rows)],
        {"lignes": precis_rows})

    # Section 3 : ce qui est commun aux trois régions
    all_above = f.add("regions.count.above3", {"kind": "count", "min_games": min_g,
                      "condition": "above", "scopes": [{"regions": [r]} for r in REGIONS]},
                      "champions significativement au-dessus de 50 % dans les trois régions")
    all_below = f.add("regions.count.below3", {"kind": "count", "min_games": min_g,
                      "condition": "below", "scopes": [{"regions": [r]} for r in REGIONS]},
                      "champions significativement en dessous de 50 % dans les trois régions")
    per_region = {}
    for r in REGIONS:
        per_region[r] = f.add(f"regions.count.above.{REGION_SHORT[r].lower()}",
                              {"kind": "count", "min_games": min_g, "condition": "above",
                               "scopes": [{"regions": [r]}]},
                              f"champions significativement au-dessus de 50 % en {REGION_LABELS[r]}")
    s3 = Section(
        "commun",
        "Ce que les trois régions ont en commun : combien de champions sont au-dessus "
        "(ou en dessous) de 50 % partout à la fois, comparé au nombre par région.",
        [f.key_value(all_above, f"Champions significativement au-dessus de 50 % dans les trois régions à la fois, parmi ceux joués au moins {f.d('param.min_region')} fois dans chacune.", unit="champions")],
        [md_table(["Région", "Champions au-dessus de 50 %"],
                  [[REGION_LABELS[r], f.d(per_region[r])] for r in REGIONS])],
        {"faits_globaux": {i: f"{f.d(i)} — {f.items[i]['meaning']}"
                           for i in [all_above, all_below, *per_region.values(),
                                     "regions.count.base", "param.min_region"]}})
    plan.sections = [s1, s2, s3]
    plan.extra_limits = ("Chaque région est représentée par une seule plateforme (EUW, KR, "
                         "NA) : les autres serveurs ne sont pas couverts.")
    return plan


def plan_champions_pieges(ds: Dataset) -> StudyPlan:
    f = Facts(ds)
    patch = ds.patch
    min_pick, min_games = 0.03, 10000
    f.param("min_games", min_games, "parties minimum")
    total = ds.matches(GLOBAL)
    popular = [c for c in ds.champions()
               if ds.stats(c, GLOBAL)[0] >= min_games and ds.stats(c, GLOBAL)[0] / total >= min_pick]
    traps = sorted([c for c in popular if ds.wr(c, GLOBAL)["hi"] < 0.5],
                   key=lambda c: -ds.stats(c, GLOBAL)[0])
    if len(traps) < 3:
        raise NotFeasible("moins de 3 champions populaires sous 50 %")
    plan = StudyPlan("champions-pieges", "champions-pieges", f"Champions pièges — patch {patch}",
                     "Les champions très joués dont l'intervalle de confiance est entièrement "
                     "sous 50 % : populaires et perdants, sur des échantillons qui ne laissent pas de doute.",
                     ["Tous rôles", "Tous ranks", "EUW · KR · NA"], f, [])
    rows, precis_rows, bases = [], [], []
    for cid in traps[:10]:
        b = f.winrate(cid, GLOBAL, "toutes régions, tous ranks")
        p = f.rate(cid, GLOBAL, "pick_rate", "toutes régions, tous ranks")
        rows.append([ds.names[cid], f.d(p), f.cell(b), f.d(b + ".games")])
        precis_rows.append(_row_precis(f, cid, [p, *_wr_ids(b)]))
        bases.append((ds.names[cid], b))
    top = traps[0]
    s1 = Section(
        "pieges",
        "Les champions joués dans au moins 3 % des parties dont tout l'intervalle de "
        "confiance est sous 50 %. Ce sont des mesures de parties perdues, pas des jugements "
        "sur le champion : ne pas conseiller de l'éviter.",
        [f.key_figure(f.winrate(top, GLOBAL, "toutes régions, tous ranks"),
                      f"Winrate de {ds.names[top]}, le champion le plus joué dont l'intervalle est entièrement sous 50 %.")],
        [md_table(["Champion", "Pick rate", "Winrate (IC 95 %)", "Parties"], rows),
         chart(f, plan.charts, bases, "pieges", "Winrate des champions populaires sous 50 %",
               "toutes régions, tous ranks")],
        {"lignes": precis_rows, "nombre_de_champions_pieges": len(traps)})

    rows, precis_rows = [], []
    lo_b, hi_b = {"buckets": ["IRON_BRONZE"]}, {"buckets": ["DIAMOND_PLUS"]}
    for cid in traps[:8]:
        a = f.winrate(cid, lo_b, "Fer–Bronze")
        z = f.winrate(cid, hi_b, "Diamant+")
        d = f.diff(cid, hi_b, lo_b, "Diamant+ moins Fer–Bronze")
        rows.append([ds.names[cid], f"{f.cell(a)} ({f.d(a + '.games')})",
                     f"{f.cell(z)} ({f.d(z + '.games')})", f.d(d)])
        precis_rows.append(_row_precis(
            f, cid, [*_wr_ids(a), *_wr_ids(z), d],
            lecture_fer_bronze=VERDICT[significance(ds.wr(cid, lo_b))],
            lecture_diamant=VERDICT[significance(ds.wr(cid, hi_b))]))
    s2 = Section(
        "niveaux",
        "Les mêmes champions en Fer–Bronze et en Diamant+ : le piège vaut-il à tous les "
        "niveaux ? Rappeler que le bucket est celui du joueur échantillonné.",
        [], [md_table(["Champion", "Fer–Bronze", "Diamant+", "Écart"], rows)],
        {"lignes": precis_rows})

    counts = {}
    for cond, label in (("above", "au-dessus de 50 %"), ("below", "en dessous de 50 %"),
                        ("contains", "indistinguables de 50 %")):
        counts[cond] = f.add(f"global.count.{cond}", {"kind": "count", "scopes": [GLOBAL],
                             "min_games": min_games, "condition": cond},
                             f"champions joués au moins le seuil de parties et {label}")
    s3 = Section(
        "ensemble",
        "Remettre les pièges en perspective : parmi tous les champions à gros volume, "
        "combien sont au-dessus, en dessous, ou indistinguables de 50 %.",
        [f.key_value(counts["below"], f"Champions joués au moins {f.d('param.min_games')} fois dont l'intervalle est entièrement sous 50 %.", unit="champions")],
        [md_table(["Lecture", "Champions"],
                  [["Au-dessus de 50 %", f.d(counts["above"])],
                   ["En dessous de 50 %", f.d(counts["below"])],
                   ["Indistinguables de 50 %", f.d(counts["contains"])]])],
        {"faits_globaux": {i: f"{f.d(i)} — {f.items[i]['meaning']}"
                           for i in [*counts.values(), "param.min_games"]}})
    plan.sections = [s1, s2, s3]
    return plan


def plan_bans(ds: Dataset) -> StudyPlan:
    f = Facts(ds)
    patch = ds.patch
    total = ds.matches(GLOBAL)
    ban_rate = lambda c: ds.stats(c, GLOBAL)[2] / total
    by_ban = sorted([c for c in ds.champions() if ds.stats(c, GLOBAL)[0] >= 2000],
                    key=lambda c: -ban_rate(c))
    if len(by_ban) < 10:
        raise NotFeasible("moins de 10 champions bannis avec un volume suffisant")
    plan = StudyPlan("bans-justifies", "bans", f"Les bans sont-ils justifiés ? Patch {patch}",
                     "Confronter le ban rate au winrate : quels champions bannis gagnent "
                     "réellement leurs parties, et lesquels non.",
                     ["Bans", "Tous ranks", "EUW · KR · NA"], f, [])
    rows, precis_rows, bases = [], [], []
    for cid in by_ban[:10]:
        b = f.winrate(cid, GLOBAL, "toutes régions, tous ranks")
        br = f.rate(cid, GLOBAL, "ban_rate", "toutes régions, tous ranks")
        verdict = VERDICT[significance(ds.wr(cid, GLOBAL))]
        rows.append([ds.names[cid], f.d(br), f.cell(b), f.d(b + ".games"), verdict])
        precis_rows.append(_row_precis(f, cid, [br, *_wr_ids(b)], lecture=verdict))
        bases.append((ds.names[cid], b))
    top = by_ban[0]
    kf = f.rate(top, GLOBAL, "ban_rate", "toutes régions, tous ranks")
    n_below = sum(1 for c in by_ban[:10] if significance(ds.wr(c, GLOBAL)) == "below")
    s1 = Section(
        "plus-bannis",
        "Les dix champions les plus bannis et leur winrate quand ils sont joués. Le champ "
        "« lecture » dit si l'intervalle est au-dessus, en dessous ou indistinguable de 50 %. "
        "Ne pas prêter d'intention aux joueurs qui bannissent.",
        [f.key_value(kf, f"Ban rate de {ds.names[top]}, le champion le plus banni du patch.")],
        [md_table(["Champion", "Ban rate", "Winrate (IC 95 %)", "Parties", "Lecture"], rows),
         chart(f, plan.charts, bases, "plus-bannis", "Winrate des dix champions les plus bannis",
               "toutes régions, tous ranks")],
        {"lignes": precis_rows, "parmi_les_dix_en_dessous_de_50": n_below,
         "parmi_les_dix_au_dessus_de_50": sum(1 for c in by_ban[:10]
                                              if significance(ds.wr(c, GLOBAL)) == "above")})

    low_ban = sorted([c for c in ds.champions() if ds.stats(c, GLOBAL)[0] >= 5000
                      and significance(ds.wr(c, GLOBAL)) == "above" and ban_rate(c) < 0.02],
                     key=lambda c: -ds.wr(c, GLOBAL)["lo"])[:8]
    rows, precis_rows = [], []
    for cid in low_ban:
        b = f.winrate(cid, GLOBAL, "toutes régions, tous ranks")
        br = f.rate(cid, GLOBAL, "ban_rate", "toutes régions, tous ranks")
        rows.append([ds.names[cid], f.cell(b), f.d(b + ".games"), f.d(br)])
        precis_rows.append(_row_precis(f, cid, [br, *_wr_ids(b)]))
    before = []
    if low_ban:
        before = [f.key_figure(f.winrate(low_ban[0], GLOBAL, "toutes régions, tous ranks"),
                               f"Winrate de {ds.names[low_ban[0]]}, banni dans moins de 2 % des parties.")]
    s2 = Section(
        "peu-bannis",
        "Les champions au winrate significativement au-dessus de 50 % (au moins 5 000 parties) "
        "et bannis dans moins de 2 % des parties. S'il n'y en a aucun, le dire simplement.",
        before,
        [md_table(["Champion", "Winrate (IC 95 %)", "Parties", "Ban rate"], rows)] if rows else [],
        {"lignes": precis_rows, "nombre": len(low_ban)})

    by_bucket_rows, precis_rows = [], []
    for cid in by_ban[:6]:
        ids = [f.rate(cid, {"buckets": [b]}, "ban_rate", BUCKET_LABELS[b]) for b in BUCKETS]
        by_bucket_rows.append([ds.names[cid], *[f.d(i) for i in ids]])
        precis_rows.append(_row_precis(f, cid, ids))
    s3 = Section(
        "par-rank",
        "Le ban rate des six champions les plus bannis, bucket par bucket : les niveaux de jeu "
        "ne bannissent pas les mêmes champions dans les mêmes proportions. Rappeler que le "
        "bucket est celui du joueur échantillonné.",
        [], [md_table(["Champion", *[BUCKET_LABELS[b] for b in BUCKETS]], by_bucket_rows)],
        {"lignes": precis_rows})
    plan.sections = [s1, s2, s3]
    return plan


def plan_rank(ds: Dataset) -> StudyPlan:
    f = Facts(ds)
    patch = ds.patch
    lo_b, hi_b = {"buckets": ["IRON_BRONZE"]}, {"buckets": ["DIAMOND_PLUS"]}
    min_g = 1500
    f.param("min_bucket", min_g, "parties minimum par bucket")
    base = [c for c in ds.champions()
            if ds.stats(c, lo_b)[0] >= min_g and ds.stats(c, hi_b)[0] >= min_g]
    if len(base) < 20:
        raise NotFeasible("moins de 20 champions assez joués en Fer–Bronze et en Diamant+")
    gap = lambda c: ds.wr(c, hi_b)["wr"] - ds.wr(c, lo_b)["wr"]
    net = lambda c: ds.wr(c, hi_b)["lo"] > ds.wr(c, lo_b)["hi"] or ds.wr(c, lo_b)["lo"] > ds.wr(c, hi_b)["hi"]
    plan = StudyPlan("tierlist-par-rank", "tierlist-rank", f"Tier list par rank — patch {patch}",
                     "Ce qui gagne en Fer–Bronze ne gagne pas en Diamant+ : le classement "
                     "refait bucket par bucket.",
                     ["Tier list", "Par rank", "EUW · KR · NA"], f, [])

    def gap_section(key, champs, consigne, kf_label, chart_id, chart_title):
        rows, precis_rows, bases = [], [], []
        for cid in champs:
            a = f.winrate(cid, lo_b, "Fer–Bronze")
            z = f.winrate(cid, hi_b, "Diamant+")
            d = f.diff(cid, hi_b, lo_b, "Diamant+ moins Fer–Bronze")
            rows.append([ds.names[cid], f"{f.cell(a)} ({f.d(a + '.games')})",
                         f"{f.cell(z)} ({f.d(z + '.games')})", f.d(d), "oui" if net(cid) else "non"])
            precis_rows.append(_row_precis(f, cid, [*_wr_ids(a), *_wr_ids(z), d],
                                           ecart_net=net(cid)))
            bases += [(f"{ds.names[cid]} — Fer–Bronze", a), (f"{ds.names[cid]} — Diamant+", z)]
        kf = f.diff(champs[0], hi_b, lo_b, "Diamant+ moins Fer–Bronze")
        return Section(key, consigne,
                       [f.key_value(kf, kf_label.format(name=ds.names[champs[0]]))],
                       [md_table(["Champion", "Fer–Bronze", "Diamant+", "Écart", "Écart net"], rows),
                        chart(f, plan.charts, bases[:8], chart_id, chart_title,
                              "intervalle de confiance à 95 %, toutes régions")],
                       {"lignes": precis_rows})

    up = sorted(base, key=lambda c: -gap(c))[:6]
    down = sorted(base, key=gap)[:6]
    s1 = gap_section("montent", up,
                     "Les champions dont le winrate est le plus haut en Diamant+ qu'en Fer–Bronze. "
                     "ecart_net indique si les deux intervalles sont disjoints. Rappeler que le "
                     "bucket est celui du joueur échantillonné.",
                     "Écart de winrate de {name} entre Diamant+ et Fer–Bronze, le plus grand du patch en faveur du haut de ladder.",
                     "montent", "Champions qui gagnent davantage en Diamant+")
    s2 = gap_section("descendent", down,
                     "Les champions dont le winrate est le plus haut en Fer–Bronze qu'en Diamant+. "
                     "Ne pas parler de champion fort ou faible sans préciser le niveau.",
                     "Écart de winrate de {name} entre Diamant+ et Fer–Bronze, le plus grand du patch en faveur du bas de ladder.",
                     "descendent", "Champions qui gagnent davantage en Fer–Bronze")
    stable_scopes = [{"buckets": [b]} for b in BUCKETS]
    stable = sorted([c for c in ds.champions()
                     if all(ds.stats(c, s)[0] >= 1000 and significance(ds.wr(c, s)) == "above"
                            for s in stable_scopes)],
                    key=lambda c: -min(ds.wr(c, s)["lo"] for s in stable_scopes))
    f.param("min_stable", 1000, "parties minimum par bucket pour la stabilité")
    n_stable = f.add("rank.count.stable", {"kind": "count", "min_games": 1000, "condition": "above",
                     "scopes": stable_scopes},
                     "champions significativement au-dessus de 50 % dans les quatre buckets")
    rows, precis_rows = [], []
    for cid in stable[:6]:
        ids, cells = [], []
        for s, b in zip(stable_scopes, BUCKETS):
            base_id = f.winrate(cid, s, BUCKET_LABELS[b])
            cells.append(f.d(base_id + ".wr"))
            ids += _wr_ids(base_id)
        rows.append([ds.names[cid], *cells])
        precis_rows.append(_row_precis(f, cid, ids))
    s3 = Section(
        "stables",
        "Les champions au-dessus de 50 % dans les quatre buckets à la fois : ceux dont le "
        "résultat ne dépend pas du niveau. S'il n'y en a aucun, le dire.",
        [f.key_value(n_stable, f"Champions significativement au-dessus de 50 % dans les quatre buckets, parmi ceux joués au moins {f.d('param.min_stable')} fois dans chacun.", unit="champions")],
        [md_table(["Champion", *[BUCKET_LABELS[b] for b in BUCKETS]], rows)] if rows else [],
        {"lignes": precis_rows, "faits_globaux": {n_stable: f"{f.d(n_stable)} — {f.items[n_stable]['meaning']}"}})
    plan.sections = [s1, s2, s3]
    return plan


def plan_role(ds: Dataset, role: str) -> StudyPlan:
    f = Facts(ds)
    patch = ds.patch
    label = ROLE_LABELS[role]
    rs = {"role": role}
    min_g = 1500
    f.param("min_role", min_g, "parties minimum au poste")
    base = [c for c in ds.champions() if ds.stats(c, rs)[0] >= min_g]
    if len(base) < 12:
        raise NotFeasible(f"moins de 12 champions joués au poste {role}")
    plan = StudyPlan(f"meta-role-{role.lower()}", f"meta-{slugify(label)}",
                     f"Méta par poste — {label}, patch {patch}",
                     f"Qui gagne réellement au poste {label}, une fois le rôle isolé.",
                     ["Méta par poste", label, "Tous ranks", "EUW · KR · NA"], f, [])
    where = f"au poste {label}"
    best = sorted(base, key=lambda c: -ds.wr(c, rs)["lo"])[:8]
    rows, precis_rows, bases = [], [], []
    for cid in best:
        b = f.winrate(cid, rs, where)
        p = f.rate(cid, rs, "pick_rate", where)
        verdict = VERDICT[significance(ds.wr(cid, rs))]
        rows.append([ds.names[cid], f.cell(b), f.d(b + ".games"), f.d(p), verdict])
        precis_rows.append(_row_precis(f, cid, [p, *_wr_ids(b)], lecture=verdict))
        bases.append((ds.names[cid], b))
    s1 = Section(
        "meilleurs",
        f"Les meilleurs winrates {where}, classés par la borne basse de l'intervalle "
        "(la valeur que l'échantillon garantit). Le pick rate est calculé sur toutes les parties.",
        [f.key_figure(f.winrate(best[0], rs, where),
                      f"Winrate de {ds.names[best[0]]} {where}, meilleure borne basse d'intervalle du poste.")],
        [md_table(["Champion", "Winrate (IC 95 %)", "Parties", "Pick rate", "Lecture"], rows),
         chart(f, plan.charts, bases, "meilleurs", f"Meilleurs winrates {where}",
               "toutes régions, tous ranks")],
        {"lignes": precis_rows})

    played = sorted(base, key=lambda c: -ds.stats(c, rs)[0])[:8]
    rows, precis_rows = [], []
    for cid in played:
        b = f.winrate(cid, rs, where)
        p = f.rate(cid, rs, "pick_rate", where)
        verdict = VERDICT[significance(ds.wr(cid, rs))]
        rows.append([ds.names[cid], f.d(p), f.cell(b), f.d(b + ".games"), verdict])
        precis_rows.append(_row_precis(f, cid, [p, *_wr_ids(b)], lecture=verdict))
    s2 = Section(
        "plus-joues",
        f"Les champions les plus joués {where} et la lecture de leur winrate. Comparer avec "
        "la section précédente sans en tirer de cause.",
        [f.key_value(f.rate(played[0], rs, "pick_rate", where),
                     f"Pick rate de {ds.names[played[0]]} {where}, le plus joué du poste.")],
        [md_table(["Champion", "Pick rate", "Winrate (IC 95 %)", "Parties", "Lecture"], rows)],
        {"lignes": precis_rows})

    lo_b, hi_b = {"role": role, "buckets": ["IRON_BRONZE"]}, {"role": role, "buckets": ["DIAMOND_PLUS"]}
    both = [c for c in base if ds.stats(c, lo_b)[0] >= 500 and ds.stats(c, hi_b)[0] >= 500]
    f.param("min_bucket_role", 500, "parties minimum par bucket au poste")
    both.sort(key=lambda c: -abs(ds.wr(c, hi_b)["wr"] - ds.wr(c, lo_b)["wr"]))
    rows, precis_rows = [], []
    for cid in both[:6]:
        a = f.winrate(cid, lo_b, f"Fer–Bronze, {where}")
        z = f.winrate(cid, hi_b, f"Diamant+, {where}")
        d = f.diff(cid, hi_b, lo_b, f"Diamant+ moins Fer–Bronze, {where}")
        net = ds.wr(cid, hi_b)["lo"] > ds.wr(cid, lo_b)["hi"] or ds.wr(cid, lo_b)["lo"] > ds.wr(cid, hi_b)["hi"]
        rows.append([ds.names[cid], f"{f.cell(a)} ({f.d(a + '.games')})",
                     f"{f.cell(z)} ({f.d(z + '.games')})", f.d(d), "oui" if net else "non"])
        precis_rows.append(_row_precis(f, cid, [*_wr_ids(a), *_wr_ids(z), d], ecart_net=net))
    s3 = Section(
        "niveaux",
        f"Les plus grands écarts entre Fer–Bronze et Diamant+ {where}. ecart_net dit si les "
        "intervalles sont disjoints. Rappeler que le bucket est celui du joueur échantillonné.",
        [], [md_table(["Champion", "Fer–Bronze", "Diamant+", "Écart", "Écart net"], rows)] if rows else [],
        {"lignes": precis_rows})
    plan.sections = [s1, s2, s3]
    return plan


TOPIC_BUILDERS = {
    "tierlist-globale": plan_tierlist,
    "comparaison-regionale": plan_regions,
    "champions-pieges": plan_champions_pieges,
    "bans-justifies": plan_bans,
    "tierlist-par-rank": plan_rank,
    **{f"meta-role-{r.lower()}": (lambda ds, r=r: plan_role(ds, r)) for r in ROLES},
}


# ---------------------------------------------------------------------------
# Prose : placeholders, contrôles, rendu
# ---------------------------------------------------------------------------

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-z0-9_.\-]+)(?::(stat))?\s*\}\}")
ALLOWED_RAW = re.compile(r"(?<![\d,])(?:50|95)\s?%")

FORBIDDEN = [
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), "émoji"),
    (re.compile(r"\b(?:tu|toi|ton|tes)\b", re.I), "tutoiement"),
    (re.compile(r"grâce à|parce qu|à cause d|\bexpliqu|\bentraîn|\bprovoqu|se tradui", re.I),
     "causalité non mesurée"),
    (re.compile(r"\b(?:énorme|cassé|incontournable|abusé|broken|OP|must[- ]pick|craqué)\b", re.I),
     "superlatif non mesuré"),
    (re.compile(r"\b(?:builds?|runes?|matchups?|items?|objets?)\b", re.I),
     "dimension non mesurée (build, rune, matchup, objet)"),
    (re.compile(r"à bannir|bannissez|jouez|il faut jouer|à éviter|évitez|choisissez", re.I),
     "conseil d'action"),
    (re.compile(r"taux de victoire", re.I), "« winrate » ne se traduit pas"),
    (re.compile(r"lol\b|league of legends", re.I), "nom du jeu dans le texte d'étude"),
]


def check_prose(text: str, facts: Facts, where: str) -> list[str]:
    errors = []
    for m in PLACEHOLDER_RE.finditer(text):
        fid, mode = m.group(1), m.group(2)
        if fid not in facts.items and not (mode == "stat" and f"{fid}.wr" in facts.items):
            errors.append(f"{where} : repère inconnu {{{{{fid}}}}}")
        if mode == "stat" and f"{fid}.wr" not in facts.items:
            errors.append(f"{where} : {{{{{fid}:stat}}}} attend l'identifiant de base d'un winrate")
    bare = PLACEHOLDER_RE.sub(" ", text)
    bare = ALLOWED_RAW.sub(" ", bare)
    for m in re.finditer(r"\d+", bare):
        ctx = bare[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
        errors.append(f"{where} : chiffre écrit en dur « {m.group()} » (…{ctx}…) — "
                      "utiliser un repère {{id}} du précis")
    for rx, label in FORBIDDEN:
        for m in rx.finditer(bare):
            errors.append(f"{where} : {label} — « {m.group()} »")
    if re.search(r"^\s*#", text, re.M):
        errors.append(f"{where} : titre markdown interdit dans le texte")
    if "<" in bare or "{" in bare or "}" in bare:
        errors.append(f"{where} : caractères < {{ }} interdits (MDX)")
    return errors


def render_prose(text: str, facts: Facts) -> str:
    def sub(m):
        fid, mode = m.group(1), m.group(2)
        if mode == "stat":
            return facts.stat(fid)
        return facts.d(fid)
    return PLACEHOLDER_RE.sub(sub, text.strip())


def build_precis(plan: StudyPlan) -> dict:
    ds = plan.facts.ds
    return {
        "titre": plan.title, "angle": plan.angle, "patch": ds.patch,
        "echantillon": {"global.matches": plan.facts.d(plan.facts.add(
            "global.matches", {"kind": "matches", "scope": GLOBAL}, "matchs du patch"))},
        "regions": [REGION_LABELS[r] for r in REGIONS],
        "sections": [{"key": s.key, "consigne": s.consigne, "donnees": s.precis}
                     for s in plan.sections],
    }


def prose_schema(plan: StudyPlan) -> dict:
    return {
        "type": "object", "additionalProperties": False,
        "required": ["description", "chapo", "sections", "retenir"],
        "properties": {
            "description": {"type": "string"},
            "chapo": {"type": "string"},
            "retenir": {"type": "string"},
            "sections": {
                "type": "array", "minItems": len(plan.sections),
                "maxItems": len(plan.sections),
                "items": {"type": "object", "additionalProperties": False,
                          "required": ["key", "titre", "texte"],
                          "properties": {"key": {"type": "string"},
                                         "titre": {"type": "string"},
                                         "texte": {"type": "string"}}},
            },
        },
    }


def apply_prose(plan: StudyPlan, prose: dict) -> list[str]:
    """Contrôle la prose du modèle et la pose sur le plan ; rend les erreurs."""
    f = plan.facts
    errors = []
    keys = [s.key for s in plan.sections]
    got = [s.get("key") for s in prose.get("sections", [])]
    if got != keys:
        errors.append(f"sections attendues dans l'ordre {keys}, reçues {got}")
        return errors
    errors += check_prose(prose["description"], f, "description")
    if len(prose["description"]) > 260:
        errors.append("description : plus de 260 caractères")
    errors += check_prose(prose["chapo"], f, "chapo")
    errors += check_prose(prose["retenir"], f, "retenir")
    for sec, out in zip(plan.sections, prose["sections"]):
        errors += check_prose(out["titre"], f, f"titre de {sec.key}")
        errors += check_prose(out["texte"], f, f"texte de {sec.key}")
        if "{{" in out["titre"]:
            errors.append(f"titre de {sec.key} : pas de repère dans un titre")
    if errors:
        return errors
    plan.description = render_prose(prose["description"], f).replace("**", "")
    plan.chapo = render_prose(prose["chapo"], f)
    plan.retenir = render_prose(prose["retenir"], f)
    for sec, out in zip(plan.sections, prose["sections"]):
        sec.titre = out["titre"].strip()
        sec.texte = render_prose(out["texte"], f)
    return []


def render_mdx(plan: StudyPlan) -> str:
    f = plan.facts
    parts = [f"# {plan.title}", "<StudyMeta />", "## Comment lire cette étude"]
    if plan.prose:
        parts.append(f"<Chapo>\n{plan.chapo}\n</Chapo>")
    ex = _intro_example(f, GLOBAL, 5000)
    lecture = ("Chaque winrate est donné avec son **intervalle de confiance de Wilson à 95 %**, "
               "calculé sur le nombre réel de parties.")
    if "above" in ex:
        cid, b = ex["above"]
        lecture += (f" {f.ds.names[cid]}, à {f.stat(b)} sur {f.d(b + '.games')} parties, est "
                    "réellement au-dessus de 50 % : tout l'intervalle l'est.")
    if "contains" in ex:
        cid, b = ex["contains"]
        lecture += (f" {f.ds.names[cid]}, à {f.stat(b)}, ne permet pas de conclure : son "
                    "intervalle contient encore 50 %.")
    lecture += (" Quand deux intervalles se recouvrent, l'écart entre deux champions n'est "
                "pas un classement.\n\nLe niveau d'une partie est approximé par le bucket du "
                "joueur échantillonné qui l'a fait découvrir, pas par le rang moyen des dix "
                "joueurs (voir la [méthodologie](/methodologie)).")
    parts.append(lecture)
    for sec in plan.sections:
        parts.append(f"## {sec.titre}")
        parts += sec.before
        parts.append(sec.texte)
        parts += sec.after
    parts.append("## Le tableau complet")
    parts.append("Trier par winrate, pick rate, ban rate ou volume ; filtrer par bucket de rank, "
                 "région et poste. Les agrégats recombinent parties et victoires des cellules, "
                 "jamais une moyenne de pourcentages, et recalculent l'intervalle de confiance "
                 "sur l'échantillon obtenu.")
    parts.append("<TierTable />")
    if plan.prose:
        parts.append("## Ce qu'il faut retenir")
        parts.append(plan.retenir)
    limits = ("Ces chiffres décrivent des **corrélations, pas des recommandations de jeu** : "
              "un winrate peut refléter le champion, la population qui le choisit ou les "
              "compositions dans lesquelles il apparaît. Cette étude ne mesure ni les builds, "
              "ni les runes, ni les matchups.\n\nLe bucket d'une partie est celui du joueur "
              "échantillonné qui l'a fait découvrir au collecteur, pas le rang moyen des dix "
              "joueurs. Les biais d'échantillonnage connus sont décrits dans la "
              "[méthodologie](/methodologie).")
    if plan.extra_limits:
        limits += " " + plan.extra_limits
    if plan.prose:
        limits += ("\n\n*Le texte de cette étude est rédigé automatiquement à partir des données "
                   "ci-dessus ; chaque chiffre est calculé et vérifié par le code, jamais saisi "
                   "par le rédacteur.*")
    else:
        limits += ("\n\n*Étude générée automatiquement à partir des données du patch ; chaque "
                   "chiffre est calculé et vérifié par le code.*")
    parts.append("## Limites")
    parts.append(limits)
    return "\n\n".join(p for p in parts if p) + "\n"


def study_payload(plan: StudyPlan, model: str | None) -> dict:
    return {
        "topic": plan.topic, "patch": plan.facts.ds.patch,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "writer_model": model if plan.prose else None,
        "facts": {k: {kk: vv for kk, vv in v.items()} for k, v in plan.facts.items.items()},
        "charts": plan.charts,
    }


def content_meta(plan: StudyPlan) -> dict:
    ds = plan.facts.ds
    today = time.strftime("%Y-%m-%d", time.gmtime())
    return {
        "title": plan.title, "description": plan.description, "date": today,
        "patch": ds.patch, "patch_sensitive": True, "sample_size": ds.total_matches,
        "regions": list(REGIONS),
        "collected_at": time.strftime("%Y-%m-%d", time.gmtime(ds.last)) if ds.last else today,
        "tags": plan.tags, "generated": True,
    }


def write_json(path: str, payload, compact: bool = False) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        if compact:
            json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
