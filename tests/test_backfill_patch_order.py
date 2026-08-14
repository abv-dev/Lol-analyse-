#!/usr/bin/env python3
"""Ordre de traitement des patchs dans `backfill.candidates()`.

L'intention documentée est « patch courant d'abord, puis les patchs
précédents du plus récent au plus ancien ». La liste venait d'un
`ORDER BY patch DESC` SQL, donc d'un tri lexicographique : « 16.9 » y passe
avant « 16.16 » et le backfill traitait 16.9 → 16.2 avant 16.15.

Tout se joue sur une base fixture construite dans `tmp_path` : `data/matches.db`
n'est jamais ouverte ici, le collecteur tourne dessus.
"""

import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from lolcollector.backfill import candidates, patch_sort_key  # noqa: E402
from lolcollector.db import Database  # noqa: E402

# Volontairement dans le désordre, et dans un désordre que le tri lexicographique
# ne corrige pas : trié comme du texte, 16.9 arrive en tête.
PATCHES = ["16.15", "16.2", "16.16", "16.9", "16.10"]
ATTENDU_DECROISSANT = ["16.16", "16.15", "16.10", "16.9", "16.2"]


def build_db(path, patches=PATCHES, current=None, per_patch=2):
    """Base fixture : `per_patch` matchs par patch, aucune timeline connue.

    Les match_id encodent leur patch (`M_16.15_0`), ce qui permet de lire
    l'ordre de traitement directement dans le retour de `candidates()`.
    """
    db = Database(str(path))
    now = int(time.time())
    for patch in patches:
        for i in range(per_patch):
            db.conn.execute(
                "INSERT OR IGNORE INTO matches (match_id, region, platform,"
                " game_version, patch, game_duration, game_creation,"
                " tier_bucket_source, inserted_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"M_{patch}_{i}", "europe", "euw1", f"{patch}.1.1", patch,
                 1800, 0, "SILVER_GOLD", now))
    db.conn.commit()
    if current is not None:
        db.set_meta("ddragon_current", current)
    return db


def ordre_des_patchs(selection):
    """Suite des patchs dans l'ordre de traitement, sans répétition."""
    ordre = []
    for match_id, _region in selection:
        patch = match_id.split("_")[1]
        if not ordre or ordre[-1] != patch:
            ordre.append(patch)
    return ordre


# --------------------------------------------------------------------------
# 1) La clé de tri elle-même
# --------------------------------------------------------------------------

def test_cle_de_tri_numerique():
    assert sorted(PATCHES, key=patch_sort_key) == list(reversed(ATTENDU_DECROISSANT))
    # le cas explicitement visé par le correctif
    assert patch_sort_key("16.9") < patch_sort_key("16.15") < patch_sort_key("16.16")
    # ... que le tri lexicographique se trompe : il place 16.9 en tête en DESC
    assert sorted(PATCHES, reverse=True)[0] == "16.9"


def test_cle_de_tri_patch_inattendu():
    """Un patch au format inattendu ne fait pas échouer le tri et passe
    derrière tous les patchs valides en ordre décroissant."""
    for bizarre in ("PBE", "16", "", "16.x", "beta.1"):
        assert patch_sort_key(bizarre) < patch_sort_key("0.0"), bizarre
    # comparables entre eux : sorted() ne lève pas
    assert sorted(["PBE", "16.15", "16", "16.9"], key=patch_sort_key, reverse=True)[:2] \
        == ["16.15", "16.9"]


# --------------------------------------------------------------------------
# 2) L'ordre effectif de `candidates()`
# --------------------------------------------------------------------------

def test_candidates_traite_les_patchs_du_plus_recent_au_plus_ancien(tmp_path):
    db = build_db(tmp_path / "ordre.db")
    try:
        selection = candidates(db, 10_000, 1.0)
        assert ordre_des_patchs(selection) == ATTENDU_DECROISSANT
    finally:
        db.close()


def test_candidates_place_le_patch_courant_en_tete(tmp_path):
    """Le patch courant passe devant, même s'il n'est pas le plus récent en
    base ; les suivants restent triés numériquement."""
    db = build_db(tmp_path / "courant.db", current="16.2.1")
    try:
        assert ordre_des_patchs(candidates(db, 10_000, 1.0)) == [
            "16.2", "16.16", "16.15", "16.10", "16.9"]
    finally:
        db.close()


def test_candidates_patch_inattendu_traite_en_dernier(tmp_path):
    """Un patch non numérique n'est ni perdu ni bloquant : il est traité,
    mais après tous les patchs valides."""
    db = build_db(tmp_path / "bizarre.db", patches=PATCHES + ["PBE", "16"])
    try:
        ordre = ordre_des_patchs(candidates(db, 10_000, 1.0))
        assert ordre[:len(ATTENDU_DECROISSANT)] == ATTENDU_DECROISSANT
        assert sorted(ordre[len(ATTENDU_DECROISSANT):]) == ["16", "PBE"]
    finally:
        db.close()


def test_limite_atteinte_pendant_les_patchs_recents(tmp_path):
    """Conséquence concrète du bug : avec une limite basse, seuls les patchs
    les plus récents doivent être servis — jamais 16.9 avant 16.15."""
    db = build_db(tmp_path / "limite.db", per_patch=4)
    try:
        selection = candidates(db, 6, 1.0)
        assert len(selection) == 6
        assert ordre_des_patchs(selection) == ["16.16", "16.15"]
    finally:
        db.close()
