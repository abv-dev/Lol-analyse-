#!/usr/bin/env python3
"""Garde-fou : vérifie que chaque nombre d'une étude existe dans ses données.

    python3 scripts/verify_study.py site/content/etudes/tierlist/16-15
    python3 scripts/verify_study.py site/content/etudes/tierlist/16-15 --verbose

Sort en code 1 dès qu'un nombre du texte ne se retrouve pas dans le JSON —
c'est le garde-fou de la promesse EloLab (« des données, pas des
impressions », voir docs/editorial.md).

La vérification est CONTEXTUELLE : un nombre n'est pas comparé à l'ensemble
de toutes les valeurs du dataset (avec des milliers de cellules, n'importe
quel pourcentage y existerait), mais aux seules valeurs des champions
mentionnés dans son paragraphe — ou dans sa ligne s'il s'agit d'un tableau.
Un winrate recopié du mauvais champion est donc détecté.

Pour chaque champion, sont acceptés : winrate, bornes de l'IC de Wilson à
95 %, parties, victoires, bans, pick rate et ban rate — sur chaque découpage
(global, par bucket, par région, bucket × région) — ainsi que les écarts de
winrate entre deux découpages. Hors contexte champion : totaux, effectifs de
cellules, comptes de significativité, écarts moyens, pick rates cumulés.

Les nombres structurels (numéro de patch, « 95 % » de l'IC, seuil de 50 %,
petits entiers d'énumération) sont exemptés ; --verbose les liste.
"""

import argparse
import json
import math
import os
import re
import sys
from collections import defaultdict

Z_95 = 1.959963984540054
TOLERANCE = 0.011  # tolérance d'arrondi sur les valeurs citées

# Nombres qui ne sont pas des mesures : conventions statistiques et
# énumérations de structure (« top 10 », « trois sections », rangs…).
ALLOWED_STRUCTURAL = {50.0, 95.0, 100.0} | {float(n) for n in range(0, 16)}


def wilson_ci(wins: int, games: int) -> tuple[float, float]:
    if games == 0:
        return (0.0, 0.0)
    phat = wins / games
    z2 = Z_95 * Z_95
    denom = 1 + z2 / games
    centre = phat + z2 / (2 * games)
    margin = Z_95 * math.sqrt((phat * (1 - phat) + z2 / (4 * games)) / games)
    return ((centre - margin) / denom, (centre + margin) / denom)


def load_study(study_dir: str):
    """Localise le MDX (contenu) et les JSON (données) d'une étude."""
    mdx_path = os.path.join(study_dir, "index.mdx")
    if not os.path.exists(mdx_path):
        raise SystemExit(f"index.mdx introuvable dans {study_dir}")

    # content/etudes/<famille>/<patch> -> data/etudes/<famille>/<patch>
    parts = os.path.normpath(study_dir).split(os.sep)
    try:
        idx = len(parts) - 1 - parts[::-1].index("content")
    except ValueError:
        raise SystemExit(f"{study_dir} n'est pas sous un répertoire content/")
    data_dir = os.sep.join(parts[:idx] + ["data"] + parts[idx + 1:])
    tier_path = os.path.join(data_dir, "tierlist.json")
    meta_path = os.path.join(data_dir, "meta.json")
    for path in (tier_path, meta_path):
        if not os.path.exists(path):
            raise SystemExit(f"données manquantes : {path}")

    with open(mdx_path, encoding="utf-8") as fh:
        mdx = fh.read()
    with open(tier_path, encoding="utf-8") as fh:
        rows = json.load(fh)
    with open(meta_path, encoding="utf-8") as fh:
        meta = json.load(fh)
    return mdx, rows, meta, data_dir


class StudyValues:
    """Valeurs citables, indexées par champion (et hors champion)."""

    def __init__(self, rows, meta):
        self.per_champion: dict[str, set[float]] = defaultdict(set)
        self.global_values: set[float] = set()
        self.slices: dict[tuple[str, str], dict[str, list]] = defaultdict(
            lambda: defaultdict(lambda: [0, 0, 0]))
        # (champion, winrate%, ic_bas%, ic_haut%) de chaque découpage : sert à
        # valider un triplet « X % [a – b] » comme un tout
        self.triplets: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
        self._build(rows, meta)

    @staticmethod
    def _pcts(value: float) -> set[float]:
        return {round(value * 100, 2), round(value * 100, 1)}

    def _build(self, rows, meta) -> None:
        cells = {(c["region"], c["bucket"]): c["matches"] for c in meta["cells"]}
        buckets = sorted({b for _, b in cells})
        regions = sorted({r for r, _ in cells})
        total = meta["total_matches"]

        # ---- dénominateurs de chaque découpage
        denoms = {("ALL", "ALL"): total}
        for bucket in buckets:
            denoms[("ALL", bucket)] = sum(m for (_, b), m in cells.items() if b == bucket)
        for region in regions:
            denoms[(region, "ALL")] = sum(m for (r, _), m in cells.items() if r == region)
        denoms.update({key: count for key, count in cells.items()})

        self.global_values.update(float(v) for v in denoms.values())
        self.global_values.add(float(meta.get("min_cell_games", 200)))
        # seuils de volume couramment cités pour qualifier un échantillon
        self.global_values.update({1000.0, 3000.0, 5000.0, 10000.0})

        # ---- agrégats champion × découpage
        # Découpages citables : global, par bucket, par région. Les cellules
        # fines région × bucket ne sont pas acceptées comme source d'un
        # chiffre du texte (échantillons faibles, et elles multiplieraient
        # les valeurs au point de rendre la vérification permissive) : le
        # tableau interactif est là pour ça.
        for row in rows:
            champ = row["champion_name"]
            for key in (("ALL", "ALL"), ("ALL", row["bucket"]),
                        (row["region"], "ALL")):
                acc = self.slices[key][champ]
                acc[0] += row["games"]
                acc[1] += row["wins"]
                acc[2] += row["bans"]
        slices = self.slices

        winrates: dict[tuple[str, str], dict[str, float]] = {}
        for key, champs in slices.items():
            denom = denoms.get(key, 0)
            wr_map: dict[str, float] = {}
            sig_above = sig_below = indistinct = 0
            for champ, (games, wins, bans) in champs.items():
                values = self.per_champion[champ]
                values.update({float(games), float(wins), float(bans)})
                if games:
                    wr = wins / games
                    wr_map[champ] = wr
                    values.update(self._pcts(wr))
                    low, high = wilson_ci(wins, games)
                    values.update(self._pcts(low))
                    values.update(self._pcts(high))
                    # demi-largeur de l'IC (« les intervalles font ±0,31 point »)
                    values.update(self._pcts((high - low) / 2))
                    self.triplets[champ].append(
                        (round(wr * 100, 2), round(low * 100, 2), round(high * 100, 2)))
                    if low > 0.5:
                        sig_above += 1
                    elif high < 0.5:
                        sig_below += 1
                    else:
                        indistinct += 1
                if denom:
                    values.update(self._pcts(games / denom))
                    values.update(self._pcts(bans / denom))
            winrates[key] = wr_map
            self.global_values.update(
                {float(len(champs)), float(sig_above), float(sig_below), float(indistinct)})

        # ---- écarts de winrate entre découpages comparables
        # Uniquement les comparaisons éditorialement pertinentes : global vs
        # chaque bucket, et les buckets entre eux (Fer–Bronze vs Diamant+).
        bucket_keys = [("ALL", b) for b in buckets] + [("ALL", "ALL")]
        pairs = [(a, b) for i, a in enumerate(bucket_keys) for b in bucket_keys[i + 1:]]
        for key_a, key_b in pairs:
                wr_a, wr_b = winrates[key_a], winrates[key_b]
                common = wr_a.keys() & wr_b.keys()
                for champ in common:
                    delta = abs(wr_b[champ] - wr_a[champ])
                    self.per_champion[champ].update(self._pcts(delta))
                for min_games in (0, 1000, 3000, 5000):
                    deltas = [
                        abs(wr_b[c] - wr_a[c]) for c in common
                        if slices[key_a][c][0] >= min_games
                        and slices[key_b][c][0] >= min_games
                    ]
                    if not deltas:
                        continue
                    self.global_values.update(self._pcts(sum(deltas) / len(deltas)))
                    self.global_values.add(float(len(deltas)))
                    for threshold in (0.01, 0.02, 0.03, 0.04, 0.05):
                        self.global_values.add(
                            float(sum(1 for d in deltas if d >= threshold)))

        # ---- pick rates cumulés du top N (« 155 % de présence cumulée »)
        ordered = sorted(slices[("ALL", "ALL")].items(), key=lambda kv: -kv[1][0])
        running = 0
        for _, (games, _, _) in ordered[:30]:
            running += games
            self.global_values.update(self._pcts(running / total))

    def allowed_for(self, champions: set[str]) -> set[float]:
        allowed = set(self.global_values)
        for champ in champions:
            allowed |= self.per_champion.get(champ, set())
        return allowed

    def triplet_matches(self, champions: set[str],
                        wr: float, low: float, high: float) -> bool:
        """Un « X % [a – b] » doit correspondre à UN découpage d'UN champion
        du contexte : les trois valeurs ensemble, pas chacune de son côté."""
        for champ in champions:
            for cand_wr, cand_low, cand_high in self.triplets.get(champ, []):
                if (abs(wr - cand_wr) <= TOLERANCE
                        and abs(low - cand_low) <= TOLERANCE
                        and abs(high - cand_high) <= TOLERANCE):
                    return True
        return False

    @property
    def champions(self) -> set[str]:
        return set(self.per_champion)


STRIP_PATTERNS = [
    (re.compile(r"```.*?```", re.S), " "),        # blocs de code
    (re.compile(r"<[^>]+>"), " "),                 # composants JSX (top={14}…)
    (re.compile(r"\]\([^)]*\)"), "] "),             # cibles de liens
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), " "),   # dates ISO
]
NUMBER_RE = re.compile(r"\d[\d   ]*(?:[.,]\d+)?")
# « 52,67 % `[52,19 – 53,15]` » : winrate suivi de son intervalle
# Le segment entre le winrate et son intervalle ne peut contenir ni « % » ni
# « | » : sinon on capturerait le pick rate d'une ligne de tableau au lieu du
# winrate qui précède réellement l'intervalle.
TRIPLET_RE = re.compile(
    r"(\d+[.,]\d+)\s*%[^\[\n%|]{0,20}\[\s*(\d+[.,]\d+)\s*(?:%\s*)?[–\-—]\s*(\d+[.,]\d+)")


def to_float(raw: str) -> float:
    return float(re.sub(r"[    ]", "", raw).replace(",", "."))


def strip_noise(mdx: str) -> str:
    text = mdx
    for pattern, replacement in STRIP_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def find_champions(text: str, champions: set[str]) -> set[str]:
    return {champ for champ in champions if champ in text}


def iter_blocks(text: str):
    """Découpe le texte en blocs : paragraphe ou tableau markdown.

    Rend (type, lignes) où type vaut "para" ou "table". Une ligne de tableau
    qui nomme un champion est vérifiée contre ce champion seul ; sinon elle
    hérite du contexte du tableau et du paragraphe qui l'introduit (cas d'un
    tableau Fer–Bronze / Diamant+ pour un champion annoncé au-dessus)."""
    lines = text.splitlines()
    buffer: list[tuple[int, str]] = []
    kind = "para"
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        is_table = stripped.startswith("|")
        if not stripped:
            if buffer:
                yield kind, buffer
                buffer = []
            kind = "para"
            continue
        if is_table != (kind == "table") and buffer:
            yield kind, buffer
            buffer = []
        kind = "table" if is_table else "para"
        buffer.append((line_no, line))
    if buffer:
        yield kind, buffer


def verify(study_dir: str, verbose: bool = False) -> int:
    mdx, rows, meta, data_dir = load_study(study_dir)
    values = StudyValues(rows, meta)
    text = strip_noise(mdx)
    patch_variants = {meta["patch"], meta["patch"].replace(".", ","),
                      meta["patch"].replace(".", "-")}

    unmatched: list[tuple[str, int, str, set[str]]] = []
    checked = exempted = 0

    previous_context: set[str] = set()
    for kind, block_lines in iter_blocks(text):
        block_text = "\n".join(line for _, line in block_lines)
        block_champions = find_champions(block_text, values.champions)
        if kind == "table":
            # un tableau hérite du paragraphe qui l'introduit
            block_champions |= previous_context
        else:
            previous_context = block_champions

        for line_no, line in block_lines:
            # une ligne de tableau qui nomme un champion n'est vérifiée que
            # contre celui-ci : un winrate recopié d'une autre ligne échoue
            line_champions = find_champions(line, values.champions)
            champions = line_champions if (kind == "table" and line_champions) \
                else block_champions
            allowed = values.allowed_for(champions)

            # 1) triplets « winrate [borne – borne] » : validés comme un tout
            triplet_numbers: set[str] = set()
            for match in TRIPLET_RE.finditer(line):
                wr, low, high = (to_float(g) for g in match.groups())
                if values.triplet_matches(champions, wr, low, high):
                    triplet_numbers.update(match.groups())
                    checked += 3
                    if verbose:
                        ctx = ", ".join(sorted(champions)) or "hors champion"
                        print(f"  OK ligne {line_no}: {wr} % [{low} – {high}]  [{ctx}]")
                else:
                    ctx_label = ", ".join(sorted(champions)) or "aucun champion en contexte"
                    unmatched.append((f"{match.group(1)} % [{match.group(2)} – "
                                      f"{match.group(3)}]", line_no, line.strip(),
                                      champions))
                    triplet_numbers.update(match.groups())
                    checked += 3
            # 2) nombres restants : vérifiés contre les valeurs du contexte
            for match in NUMBER_RE.finditer(line):
                raw = match.group(0).strip()
                if not raw or any(v in match.group(0) for v in patch_variants):
                    continue
                if raw in triplet_numbers:   # déjà validé dans son triplet
                    continue
                normalised = re.sub(r"[    ]", "", raw).replace(",", ".")
                try:
                    value = float(normalised)
                except ValueError:
                    continue
                if value in ALLOWED_STRUCTURAL:
                    exempted += 1
                    if verbose:
                        print(f"  ~ ligne {line_no}: {raw} (structurel)")
                    continue
                checked += 1
                if any(abs(value - candidate) <= TOLERANCE for candidate in allowed):
                    if verbose:
                        ctx = ", ".join(sorted(champions)) or "hors champion"
                        print(f"  OK ligne {line_no}: {raw}  [{ctx}]")
                else:
                    unmatched.append((raw, line_no, line.strip(), champions))

    print(f"Étude   : {study_dir}")
    print(f"Données : {data_dir} ({len(rows)} cellules, "
          f"{meta['total_matches']} matchs, patch {meta['patch']})")
    print(f"Nombres vérifiés : {checked} ({exempted} exemptés comme structurels)")

    if unmatched:
        print(f"\n{len(unmatched)} NOMBRE(S) INTROUVABLE(S) DANS LES DONNÉES :")
        for raw, line_no, line, champions in unmatched:
            excerpt = line if len(line) <= 120 else line[:117] + "…"
            ctx = ", ".join(sorted(champions)) or "aucun champion en contexte"
            print(f"  ✗ ligne {line_no} : « {raw} »  (contexte : {ctx})")
            print(f"    {excerpt}")
        print("\nÉCHEC — un chiffre du texte ne provient pas des données "
              "exportées (voir docs/editorial.md, règle « des données, pas "
              "des impressions »).")
        return 1

    print("\nOK — tous les chiffres du texte proviennent des données exportées.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Vérifie qu'une étude ne cite que des chiffres présents "
                    "dans ses données exportées.")
    parser.add_argument("study_dir",
                        help="dossier de l'étude, ex: site/content/etudes/tierlist/16-15")
    parser.add_argument("--verbose", action="store_true",
                        help="détaille chaque nombre vérifié et son contexte")
    args = parser.parse_args()
    return verify(args.study_dir, verbose=args.verbose)


if __name__ == "__main__":
    sys.exit(main())
