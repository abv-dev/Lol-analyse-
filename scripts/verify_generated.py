#!/usr/bin/env python3
"""Vérification d'une étude générée : chaque nombre vient des données.

    python3 scripts/verify_generated.py site/content/etudes/regions/16-18

Volontairement indépendant du générateur (stdlib seule, aucun import de
lolcollector) : il ne fait confiance à rien de ce que le générateur a calculé.

1. Chaque fait de study.json est RECALCULÉ depuis les JSON exportés
   (tierlist.json, tierlist-roles.json, meta.json) à partir de sa formule,
   puis comparé à la valeur et à l'affichage enregistrés.
2. Chaque nombre du MDX et de la description (meta.json du contenu) doit
   correspondre à un fait recalculé, DANS SON CONTEXTE : un nombre d'un
   paragraphe ou d'une ligne de tableau qui nomme un champion doit être un
   fait de ce champion ou un fait global. Un winrate recopié du mauvais
   champion est rejeté.
3. Seules exceptions : « 50 % » et « 95 % » (conventions statistiques) et le
   numéro du patch.

Code de sortie 0 si tout concorde, 1 sinon.
"""

import argparse
import json
import math
import os
import re
import sys

Z_95 = 1.959963984540054
TOLERANCE = 0.0051  # arrondi à deux décimales


def wilson(wins, games):
    if games == 0:
        return (0.0, 0.0)
    p = wins / games
    z2 = Z_95 * Z_95
    denom = 1 + z2 / games
    centre = p + z2 / (2 * games)
    margin = Z_95 * math.sqrt((p * (1 - p) + z2 / (4 * games)) / games)
    return ((centre - margin) / denom, (centre + margin) / denom)


class Source:
    def __init__(self, data_dir):
        def load(name):
            with open(os.path.join(data_dir, name), encoding="utf-8") as fh:
                return json.load(fh)
        self.rows = load("tierlist.json")
        self.roles = load("tierlist-roles.json")
        self.meta = load("meta.json")
        self.study = load("study.json")
        self.names = {r["champion_id"]: r["champion_name"] for r in self.rows}

    @staticmethod
    def _ok(region, bucket, scope):
        return ((not scope.get("regions") or region in scope["regions"])
                and (not scope.get("buckets") or bucket in scope["buckets"]))

    def matches(self, scope):
        return sum(c["matches"] for c in self.meta["cells"]
                   if self._ok(c["region"], c["bucket"], scope))

    def stats(self, cid, scope):
        games = wins = bans = 0
        for r in self.rows:
            if r["champion_id"] == cid and self._ok(r["region"], r["bucket"], scope):
                bans += r["bans"]
                if not scope.get("role"):
                    games += r["games"]
                    wins += r["wins"]
        if scope.get("role"):
            for r in self.roles:
                if (r["champion_id"] == cid and r["role"] == scope["role"]
                        and self._ok(r["region"], r["bucket"], scope)):
                    games += r["games"]
                    wins += r["wins"]
        return games, wins, bans

    def value(self, spec):
        kind, scope = spec["kind"], spec.get("scope", {})
        if kind == "param":
            return spec["value"]
        if kind == "matches":
            return self.matches(scope)
        if kind == "count":
            n = 0
            for cid in self.names:
                ok = True
                for sc in spec["scopes"]:
                    g, w, _ = self.stats(cid, sc)
                    if g < spec["min_games"]:
                        ok = False
                        break
                    lo, hi = wilson(w, g)
                    c = spec["condition"]
                    if (c == "above" and lo <= 0.5) or (c == "below" and hi >= 0.5) \
                            or (c == "contains" and not lo <= 0.5 <= hi):
                        ok = False
                        break
                n += ok
            return n
        if kind == "wr_diff":
            ga, wa, _ = self.stats(spec["champion_id"], scope)
            gb, wb, _ = self.stats(spec["champion_id"], spec["minus"])
            return wa / ga - wb / gb
        g, w, b = self.stats(spec["champion_id"], scope)
        if kind == "games":
            return g
        if kind == "wins":
            return w
        if kind == "bans":
            return b
        if kind == "winrate":
            return w / g
        if kind == "ci_low":
            return wilson(w, g)[0]
        if kind == "ci_high":
            return wilson(w, g)[1]
        if kind == "pick_rate":
            return g / self.matches(scope)
        if kind == "ban_rate":
            return b / self.matches(scope)
        raise ValueError(f"type de fait inconnu : {kind}")


def shown(kind, value):
    """Valeur telle qu'elle doit apparaître dans le texte (nombre)."""
    if kind in ("winrate", "ci_low", "ci_high", "pick_rate", "ban_rate", "wr_diff"):
        return round(value * 100, 2)
    return value


def display(kind, value):
    fr = lambda x: f"{x:.2f}".replace(".", ",")
    if kind in ("winrate", "pick_rate", "ban_rate"):
        return fr(value * 100) + " %"
    if kind in ("ci_low", "ci_high"):
        return fr(value * 100)
    if kind == "wr_diff":
        return ("+" if value >= 0 else "−") + fr(abs(value) * 100) + " pts"
    return f"{int(value):,}".replace(",", " ")


NUMBER_RE = re.compile(r"[+−-]?\d{1,3}(?:[  ]\d{3})+(?:,\d+)?|[+−-]?\d+(?:,\d+)?")


def parse(token):
    t = token.replace(" ", "").replace(" ", "").replace("−", "-").replace(",", ".")
    return float(t)


def blocks(text):
    """Paragraphes ; chaque ligne de tableau est son propre bloc."""
    out = []
    for para in re.split(r"\n\s*\n", text):
        lines = para.split("\n")
        if all(l.strip().startswith("|") for l in lines if l.strip()):
            out += [(l, "ligne de tableau") for l in lines if l.strip()]
        else:
            out.append((para, "paragraphe"))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("study_dir")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    content = os.path.normpath(args.study_dir)
    family, slug = content.split(os.sep)[-2:]
    root = content.split(os.sep + "content" + os.sep)[0]
    data_dir = os.path.join(root, "data", "etudes", family, slug)
    src = Source(data_dir)
    with open(os.path.join(content, "index.mdx"), encoding="utf-8") as fh:
        mdx = fh.read()
    with open(os.path.join(content, "meta.json"), encoding="utf-8") as fh:
        cmeta = json.load(fh)

    errors = []
    facts = src.study["facts"]

    # 1. recalcul de chaque fait
    recomputed = {}
    for fid, fact in facts.items():
        spec = fact["spec"]
        try:
            value = src.value(spec)
        except (ZeroDivisionError, KeyError, ValueError) as exc:
            errors.append(f"fait {fid} : incalculable depuis les données ({exc})")
            continue
        if abs(value - fact["value"]) > 1e-9:
            errors.append(f"fait {fid} : study.json dit {fact['value']}, les données donnent {value}")
        if display(spec["kind"], value) != fact["display"]:
            errors.append(f"fait {fid} : affichage « {fact['display']} » ≠ « {display(spec['kind'], value)} »")
        recomputed[fid] = (spec["kind"], shown(spec["kind"], value), spec.get("champion_id"))

    for cid, chart in src.study.get("charts", {}).items():
        for row in chart["rows"]:
            for key in ("wr", "lo", "hi", "games"):
                if row[key] not in facts:
                    errors.append(f"graphique {cid} : fait inconnu {row[key]}")
        if f'<IntervalChart id="{cid}" />' not in mdx:
            errors.append(f"graphique {cid} défini mais absent du MDX")
    for m in re.finditer(r'<IntervalChart id="([^"]+)" />', mdx):
        if m.group(1) not in src.study.get("charts", {}):
            errors.append(f"graphique {m.group(1)} utilisé mais non défini")

    # 2. nombres du texte, en contexte
    names = sorted(src.names.items(), key=lambda kv: -len(kv[1]))
    name_res = [(cid, re.compile(r"(?<![\w'])" + re.escape(n) + r"(?![\w'])")) for cid, n in names]
    patch = src.meta["patch"]
    checked = 0

    def check_text(text, origin):
        nonlocal checked
        for block, kind in blocks(text):
            scrubbed = block.replace(f"patch {patch}", "patch").replace(patch, "")
            scrubbed = re.sub(r"(?<![\d,])(?:50|95)\s?%", "", scrubbed)
            scrubbed = re.sub(r"\]\([^)]*\)", "]", scrubbed)  # URLs de liens
            champs = set()
            rest = scrubbed
            for cid, rx in name_res:
                if rx.search(rest):
                    champs.add(cid)
                    rest = rx.sub(" ", rest)
            for m in NUMBER_RE.finditer(rest):
                token = m.group()
                num = parse(token)
                checked += 1
                ok = False
                for fid, (k, val, fcid) in recomputed.items():
                    if fcid is not None and fcid not in champs:
                        continue
                    if k in ("games", "wins", "bans", "matches", "count", "param"):
                        if num == val:
                            ok = True
                    elif abs(abs(num) - abs(val)) < TOLERANCE and (
                            k != "wr_diff" or (num < 0) == (val < 0) or not token[0] in "+−-"):
                        ok = True
                    if ok:
                        break
                if not ok:
                    who = ", ".join(src.names[c] for c in champs) or "aucun champion"
                    line = block.strip().replace("\n", " ")[:160]
                    errors.append(f"{origin} ({kind}, contexte : {who}) : « {token} » ne "
                                  f"correspond à aucune donnée — {line}")

    check_text(mdx, "index.mdx")
    check_text(cmeta.get("description", ""), "meta.json description")

    if errors:
        print(f"ÉCHEC — {len(errors)} problème(s) :")
        for e in errors:
            print("  - " + e)
        return 1
    print(f"OK — {len(facts)} faits recalculés depuis les données, "
          f"{checked} nombres du texte vérifiés en contexte.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
