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
import math
import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field

from .export import (
    MIN_REGION_MATCHES, ROLES, Z_95, compute_tierlist, fetch_champion_names,
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
        self.conn = None
        self.metrics: dict[str, list] = {}
        self.timelines: dict[str, list] = {}
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
        ds = cls(patch, rows, role_rows, cells, first, last)
        ds.conn = conn
        return ds

    def metric_rows(self, metric: str) -> list:
        """Agrégats d'une métrique, chargés à la demande et mis en cache.

        Le chargement exige la connexion : tous les faits d'une étude sont
        donc calculés pendant que la base est ouverte, jamais après.
        """
        if metric not in self.metrics:
            if self.conn is None:
                raise RuntimeError(
                    f"métrique {metric} demandée hors connexion : les faits "
                    "doivent être calculés pendant la lecture de la base")
            self.metrics[metric] = load_metric(self.conn, self.patch, metric)
        return self.metrics[metric]

    def timeline_rows(self, event: str) -> list:
        if event not in self.timelines:
            if self.conn is None:
                raise RuntimeError(f"événement {event} demandé hors connexion")
            self.timelines[event] = load_timeline_rate(self.conn, self.patch, event)
        return self.timelines[event]

    def metric_stats(self, cid: int, metric: str, scope: dict):
        n = total = sumsq = 0.0
        for row in self.metric_rows(metric):
            if row["champion_id"] == cid and _cell_ok(row, scope):
                n += row["n"]
                total += row["sum"]
                sumsq += row["sumsq"]
        return int(n), total, sumsq

    def rate_stats(self, cid: int, event: str, scope: dict):
        games = hits = 0
        for row in self.timeline_rows(event):
            if row["champion_id"] == cid and _cell_ok(row, scope):
                games += row["games"]
                hits += row["hits"]
        return games, hits

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
# Métriques du Lot 13 et timelines : agrégats par cellule, en lecture seule
# ---------------------------------------------------------------------------

# Chaque métrique est une expression SQL sur participants, avec le format
# d'affichage de sa moyenne. `sens` dit quel côté du classement est
# remarquable ; il ne juge rien, il ordonne.
# (libellé, expression SQL, décimales, suffixe affiché, unité des composants)
METRICS = {
    "vision_score": ("vision score", "p.vision_score", 1, "", "de vision"),
    "damage_to_champions": ("dégâts aux champions", "p.damage_to_champions", 0, "",
                            "dégâts"),
    "damage_per_gold": ("dégâts aux champions par or gagné",
                        "p.damage_to_champions * 1.0 / NULLIF(p.gold_earned, 0)", 2, "",
                        "dégâts/or"),
    "cs_at10": ("CS à 10 minutes", "p.lane_cs_at10 + p.jungle_cs_at10", 1, "", "CS"),
    "time_spent_dead": ("temps passé mort", "p.time_spent_dead / 60.0", 1, " min", "min"),
    "control_wards_bought": ("pinks achetés", "p.control_wards_bought", 2, "", "pinks"),
    "turret_plates_taken": ("plates de tourelle prises", "p.turret_plates_taken", 2, "",
                            "plates"),
    "deaths": ("morts", "p.deaths", 2, "", "morts"),
    "kills": ("éliminations", "p.kills", 2, "", "kills"),
    "assists": ("passes décisives", "p.assists", 2, "", "assists"),
    "gold_earned": ("or gagné", "p.gold_earned", 0, "", "or"),
    "damage_taken": ("dégâts subis", "p.damage_taken", 0, "", "dégâts subis"),
}

METRIC_SQL = (
    "SELECT m.region, m.tier_bucket_source, p.champion_id, p.team_position,"
    " COUNT(*), SUM({expr}), SUM(({expr}) * ({expr}))"
    " FROM matches m JOIN participants p ON p.match_id = m.match_id"
    " WHERE m.patch = ? AND ({expr}) IS NOT NULL"
    " GROUP BY m.region, m.tier_bucket_source, p.champion_id, p.team_position"
)

# Dénominateur des taux de timeline : les participants des matchs dont la
# timeline a réellement été collectée (10 % des matchs, échantillonnés).
TL_GAMES_SQL = (
    "SELECT m.region, m.tier_bucket_source, p.champion_id, p.team_position, COUNT(*)"
    " FROM matches m JOIN participants p ON p.match_id = m.match_id"
    " JOIN timeline_state t ON t.match_id = m.match_id AND t.status = 'ok'"
    " WHERE m.patch = ?"
    " GROUP BY m.region, m.tier_bucket_source, p.champion_id, p.team_position"
)
# Numérateur : les participants morts au moins une fois avant 5 minutes.
TL_EARLY_DEATH_SQL = (
    "SELECT m.region, m.tier_bucket_source, p.champion_id, p.team_position,"
    " COUNT(DISTINCT p.match_id)"
    " FROM timeline_events e"
    " JOIN matches m ON m.match_id = e.match_id"
    " JOIN participants p ON p.match_id = e.match_id"
    "  AND p.participant_id = e.victim_id"
    " WHERE m.patch = ? AND e.type = 'CHAMPION_KILL' AND e.timestamp_ms < 300000"
    " GROUP BY m.region, m.tier_bucket_source, p.champion_id, p.team_position"
)
TL_EVENTS = {"morts_avant_5min": (TL_EARLY_DEATH_SQL,
                                  "part des parties où le champion meurt avant 5 minutes")}


def load_metric(conn, patch: str, metric: str) -> list[dict]:
    """Agrégats (n, somme, somme des carrés) d'une métrique, par cellule."""
    expr = METRICS[metric][1]
    return [
        {"champion_id": champ, "region": region, "bucket": bucket, "role": role,
         "n": n, "sum": total, "sumsq": sumsq}
        for region, bucket, champ, role, n, total, sumsq
        in conn.execute(METRIC_SQL.format(expr=expr), (patch,))
        if role in ROLES and n
    ]


def load_timeline_rate(conn, patch: str, event: str) -> list[dict]:
    """Parties avec timeline et parties concernées par l'événement, par cellule."""
    games = {}
    for region, bucket, champ, role, n in conn.execute(TL_GAMES_SQL, (patch,)):
        if role in ROLES:
            games[(champ, region, bucket, role)] = n
    hits = {}
    for region, bucket, champ, role, n in conn.execute(TL_EVENTS[event][0], (patch,)):
        if role in ROLES:
            hits[(champ, region, bucket, role)] = n
    return [
        {"champion_id": champ, "region": region, "bucket": bucket, "role": role,
         "games": n, "hits": hits.get((champ, region, bucket, role), 0)}
        for (champ, region, bucket, role), n in sorted(games.items())
    ]


def _cell_ok(row, scope) -> bool:
    return ((not scope.get("regions") or row["region"] in scope["regions"])
            and (not scope.get("buckets") or row["bucket"] in scope["buckets"])
            and (not scope.get("role") or row["role"] == scope["role"]))


def mean_ci(n: int, total: float, sumsq: float) -> tuple[float, float, float]:
    """Moyenne et intervalle de confiance à 95 % (erreur type × 1,96)."""
    if n <= 0:
        return (0.0, 0.0, 0.0)
    mean = total / n
    if n < 2:
        return (mean, mean, mean)
    var = max(0.0, (sumsq - n * mean * mean) / (n - 1))
    half = Z_95 * math.sqrt(var / n)
    return (mean, mean - half, mean + half)


# ---------------------------------------------------------------------------
# Faits
# ---------------------------------------------------------------------------

def fr_int(n: int) -> str:
    return f"{int(n):,}".replace(",", " ")


def fr_dec(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


PERCENT_KINDS = {"winrate", "ci_low", "ci_high", "pick_rate", "ban_rate"}
INT_KINDS = {"games", "wins", "bans", "matches", "count", "param"}


def format_fact(spec: dict, value) -> str:
    kind = spec["kind"]
    if kind in ("winrate", "pick_rate", "ban_rate", "rate"):
        return fr_dec(value * 100) + " %"
    if kind in ("ci_low", "ci_high", "rate_lo", "rate_hi"):
        return fr_dec(value * 100)
    if kind == "wr_diff":
        return ("+" if value >= 0 else "−") + fr_dec(abs(value) * 100) + " pts"
    if kind in ("mean", "mean_lo", "mean_hi", "mean_diff"):
        digits = spec.get("digits", 2)
        suffix = spec.get("suffix", "")
        shown = abs(value) if kind == "mean_diff" else value
        body = fr_int(round(shown)) if digits == 0 else fr_dec(shown, digits)
        sign = ("+" if value >= 0 else "−") if kind == "mean_diff" else ""
        return sign + body + suffix
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
    if kind in ("mean", "mean_lo", "mean_hi", "mean_n"):
        n, total, sumsq = ds.metric_stats(spec["champion_id"], spec["metric"], scope)
        if kind == "mean_n":
            return n
        if not n:
            raise ValueError(f"aucune mesure pour {spec}")
        mean, lo, hi = mean_ci(n, total, sumsq)
        return {"mean": mean, "mean_lo": lo, "mean_hi": hi}[kind]
    if kind == "mean_diff":
        a = evaluate(ds, {**spec, "kind": "mean"})
        b = evaluate(ds, {**spec, "kind": "mean", "scope": spec["minus"]})
        return a - b
    if kind in ("rate", "rate_lo", "rate_hi", "rate_n"):
        games, hits = ds.rate_stats(spec["champion_id"], spec["event"], scope)
        if kind == "rate_n":
            return games
        if not games:
            raise ValueError(f"aucune partie avec timeline pour {spec}")
        lo, hi = wilson_ci(hits, games)
        return {"rate": hits / games, "rate_lo": lo, "rate_hi": hi}[kind]
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
                               "display": format_fact(spec, value),
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

    def metric(self, cid: int, metric: str, scope: dict, label: str) -> str:
        """Moyenne d'une métrique, son IC à 95 % et son effectif."""
        mlabel, _, digits, suffix, _ = METRICS[metric]
        base = f"{self.champ_id(cid)}.{scope_key(scope)}.{metric}"
        name = self.ds.names[cid]
        common = {"champion_id": cid, "scope": scope, "metric": metric,
                  "digits": digits, "suffix": suffix}
        self.add(f"{base}.mean", {"kind": "mean", **common},
                 f"{mlabel} moyen de {name} ({label})")
        self.add(f"{base}.mean_lo", {"kind": "mean_lo", **common},
                 f"borne basse de l'IC à 95 % du {mlabel} moyen de {name} ({label})")
        self.add(f"{base}.mean_hi", {"kind": "mean_hi", **common},
                 f"borne haute de l'IC à 95 % du {mlabel} moyen de {name} ({label})")
        self.add(f"{base}.mean_n", {"kind": "mean_n", **common},
                 f"participations mesurées pour {name} ({label})")
        return base

    def metric_diff(self, cid: int, metric: str, scope: dict, minus: dict,
                    label: str) -> str:
        mlabel, _, digits, suffix, _ = METRICS[metric]
        fid = f"{self.champ_id(cid)}.{scope_key(scope)}.vs.{scope_key(minus)}.{metric}"
        return self.add(fid, {"kind": "mean_diff", "champion_id": cid, "scope": scope,
                              "minus": minus, "metric": metric, "digits": digits,
                              "suffix": suffix},
                        f"écart de {mlabel} moyen de {self.ds.names[cid]} : {label}")

    def event_rate(self, cid: int, event: str, scope: dict, label: str) -> str:
        """Taux issu des timelines (part des parties concernées), avec IC."""
        base = f"{self.champ_id(cid)}.{scope_key(scope)}.{event}"
        name = self.ds.names[cid]
        common = {"champion_id": cid, "scope": scope, "event": event}
        self.add(f"{base}.rate", {"kind": "rate", **common},
                 f"{TL_EVENTS[event][1]} pour {name} ({label})")
        self.add(f"{base}.rate_lo", {"kind": "rate_lo", **common},
                 f"borne basse de l'IC à 95 % de ce taux pour {name} ({label})")
        self.add(f"{base}.rate_hi", {"kind": "rate_hi", **common},
                 f"borne haute de l'IC à 95 % de ce taux pour {name} ({label})")
        self.add(f"{base}.rate_n", {"kind": "rate_n", **common},
                 f"parties avec timeline mesurées pour {name} ({label})")
        return base

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

    def mean_cell(self, base: str) -> str:
        return (f'{self.d(base + ".mean")} `[{self.d(base + ".mean_lo")} – '
                f'{self.d(base + ".mean_hi")}]`')

    def mean_stat(self, base: str) -> str:
        unit = METRICS[self.items[base + ".mean"]["spec"]["metric"]][4]
        return (f'<Stat value="{self.d(base + ".mean")}" unit="{unit}" '
                f'ci="{self.d(base + ".mean_lo")} – {self.d(base + ".mean_hi")}" />')

    def mean_key_figure(self, base: str, label: str) -> str:
        unit = METRICS[self.items[base + ".mean"]["spec"]["metric"]][4]
        return (f'<KeyFigure value="{self.d(base + ".mean")}" unit="{unit}" '
                f'ci="{self.d(base + ".mean_lo")} – {self.d(base + ".mean_hi")}" '
                f'label="{label}" sample="{self.d(base + ".mean_n")} participations" />')

    def rate_cell(self, base: str) -> str:
        return (f'{self.d(base + ".rate")} `[{self.d(base + ".rate_lo")} – '
                f'{self.d(base + ".rate_hi")}]`')

    def rate_stat(self, base: str) -> str:
        return (f'<Stat value="{self.d(base + ".rate").removesuffix(" %")}" '
                f'ci="{self.d(base + ".rate_lo")} – {self.d(base + ".rate_hi")}" />')

    def rate_key_figure(self, base: str, label: str) -> str:
        return (f'<KeyFigure value="{self.d(base + ".rate").removesuffix(" %")}" '
                f'ci="{self.d(base + ".rate_lo")} – {self.d(base + ".rate_hi")}" '
                f'label="{label}" '
                f'sample="{self.d(base + ".rate_n")} parties avec timeline" />')

    def key_value(self, fid: str, label: str, unit: str = "%") -> str:
        spec = self.items[fid]["spec"]
        value = self.d(fid).removesuffix(" %").removesuffix(" pts")
        if spec["kind"] == "wr_diff":
            unit = "pts"
        elif spec["kind"] == "mean_diff":
            unit = METRICS[spec["metric"]][4]
            if spec.get("suffix"):
                value = value.removesuffix(spec["suffix"])
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
