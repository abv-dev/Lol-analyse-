#!/usr/bin/env python3
"""Tests du Lot 0 — `scripts/coverage_check.py` (mesure de couverture).

Tout se joue sur une **base fixture** construite dans `tmp_path` avec le DDL
et les index du §3 de la spec : `data/matches.db` n'est jamais ouverte ici, le
collecteur tourne dessus.

Les effectifs attendus sont calculés à la main dans `FIXTURE` ci-dessous, pas
recalculés par le test à partir du même code que le script.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import coverage_check as cc  # noqa: E402

# --------------------------------------------------------------------------
# Fixture : DDL et index du §3, réduits aux colonnes utilisées par le script
# --------------------------------------------------------------------------

DDL = """
CREATE TABLE matches (
    match_id            TEXT PRIMARY KEY,
    region              TEXT,
    platform            TEXT,
    patch               TEXT,
    game_duration       INTEGER,
    tier_bucket_source  TEXT
);
CREATE TABLE participants (
    match_id       TEXT,
    puuid          TEXT,
    champion_id    INTEGER,
    champion_name  TEXT,
    team_position  TEXT,
    kills          INTEGER,
    assists        INTEGER,
    total_cs       INTEGER,
    patch          TEXT
);
CREATE TABLE timeline_state (
    match_id  TEXT PRIMARY KEY,
    status    TEXT
);
CREATE INDEX idx_participants_champ_patch ON participants (champion_id, patch);
CREATE INDEX idx_participants_match       ON participants (match_id);
CREATE INDEX idx_matches_patch_bucket     ON matches (patch, tier_bucket_source);
"""

KAISA = ("Kaisa", 145)
GALIO = ("Galio", 3)


def _new_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(DDL)
    return conn


def add_game(conn, match_id, region, bucket, patch, duration, timeline,
             targets, afk=False):
    """Insère une game complète.

    `targets` : liste de (puuid, (nom, champion_id), team_position).
    Un participant de remplissage est toujours ajouté ; `afk=True` lui donne
    le profil kills=0/assists=0/total_cs=0 qui déclenche le proxy AFK (§4.3).
    `timeline` : None, 'ok' ou 'skipped'.
    """
    conn.execute(
        "INSERT INTO matches (match_id, region, platform, patch,"
        " game_duration, tier_bucket_source) VALUES (?,?,?,?,?,?)",
        (match_id, region, "euw1", patch, duration, bucket))
    rows = [(match_id, puuid, champ[1], champ[0], position, 5, 7, 180, patch)
            for puuid, champ, position in targets]
    rows.append((match_id, f"filler_{match_id}", 64, "LeeSin", "JUNGLE",
                 0 if afk else 4, 0 if afk else 6, 0 if afk else 120, patch))
    conn.executemany(
        "INSERT INTO participants (match_id, puuid, champion_id, champion_name,"
        " team_position, kills, assists, total_cs, patch)"
        " VALUES (?,?,?,?,?,?,?,?,?)", rows)
    if timeline is not None:
        conn.execute("INSERT INTO timeline_state (match_id, status)"
                     " VALUES (?,?)", (match_id, timeline))


def build_fixture(path):
    """Base de référence : patchs 16.9 et 16.16, cellule Kaisa/BOTTOM/IRON_BRONZE
    peuplée à la main dans les trois régions."""
    conn = _new_db(path)
    n = 0

    def game(region, bucket, duration, timeline, puuid, patch="16.16",
             champ=KAISA, position="BOTTOM", afk=False):
        nonlocal n
        n += 1
        add_game(conn, f"EUW1_{n}", region, bucket, patch, duration, timeline,
                 [(puuid, champ, position)], afk=afk)

    # --- europe / IRON_BRONZE / Kaisa / BOTTOM ---
    # pu_e1 : 6 games propres (≥ 5 → compte dans n_puuid_ge5), 2 timelines ok
    for i in range(6):
        game("europe", "IRON_BRONZE", 1800,
             "ok" if i < 2 else ("skipped" if i == 2 else None), "pu_e1")
    # pu_e2 : 3 games propres (< 5), 1 timeline ok
    for i in range(3):
        game("europe", "IRON_BRONZE", 1800, "ok" if i == 0 else None, "pu_e2")
    # puuid NULL : 2 games propres, exclues du compte de joueurs
    for i in range(2):
        game("europe", "IRON_BRONZE", 1800, "ok" if i == 0 else None, None)
    # 2 games sales, toutes deux avec une timeline ok (elles ne doivent
    # compter que dans n_games) : une trop courte, une avec un AFK-proxy
    game("europe", "IRON_BRONZE", 1100, "ok", "pu_e2")
    game("europe", "IRON_BRONZE", 1800, "ok", "pu_e2", afk=True)
    # positions inexploitables : exclues des cellules, comptées à part
    game("europe", "IRON_BRONZE", 1800, None, "pu_e9", position="")
    game("europe", "IRON_BRONZE", 1800, None, "pu_e9", position=None)

    # --- asia / IRON_BRONZE ---
    for i in range(5):                       # pu_a1 : pile 5 games propres
        game("asia", "IRON_BRONZE", 1800, "ok" if i == 0 else None, "pu_a1")
    game("asia", "IRON_BRONZE", 1800, None, "pu_a2")

    # --- americas / IRON_BRONZE ---
    for _ in range(2):
        game("americas", "IRON_BRONZE", 1800, None, "pu_m1")

    # --- une autre cellule, pour vérifier qu'elles n'interfèrent pas ---
    game("europe", "SILVER_GOLD", 1800, "ok", "pu_g1", champ=GALIO,
         position="MIDDLE")

    # --- patch 16.9 : sert au tri version-aware (16.9 < 16.16) ---
    game("europe", "IRON_BRONZE", 1800, None, "pu_old", patch="16.9")

    conn.commit()
    conn.close()


# Effectifs attendus pour Kaisa / BOTTOM / IRON_BRONZE sur 16.16, calculés à
# la main : (n_games, n_clean, n_puuid, n_puuid_ge5, n_timeline)
EXPECTED_KAISA_BOTTOM_IRON = {
    "europe":   (13, 11, 2, 1, 4),
    "asia":     (6, 6, 2, 1, 1),
    "americas": (2, 2, 1, 0, 0),
    "ALL":      (21, 19, 5, 2, 5),
}


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "fixture.db"
    build_fixture(path)
    return path


def run(db_path, out_dir, *args):
    return cc.main(["--db", str(db_path), "--out", str(out_dir), *args])


def load(out_dir, patch):
    import json
    return json.loads((Path(out_dir) / f"coverage_{patch}.json").read_text())


def cell(payload, champion, position, bucket, region):
    for row in payload["rows"]:
        if (row["champion"], row["team_position"], row["bucket"],
                row["region"]) == (champion, position, bucket, region):
            return row
    raise AssertionError(f"cellule absente : {champion} {position} {bucket} {region}")


def counts(row):
    return (row["n_games"], row["n_clean"], row["n_puuid"],
            row["n_puuid_ge5"], row["n_timeline"])


# --------------------------------------------------------------------------
# 1) Comptage exact
# --------------------------------------------------------------------------

def test_comptage_exact(db, tmp_path):
    out = tmp_path / "out"
    assert run(db, out, "--patch", "16.16") == 0
    payload = load(out, "16.16")

    for region, expected in EXPECTED_KAISA_BOTTOM_IRON.items():
        row = cell(payload, "Kaisa", "BOTTOM", "IRON_BRONZE", region)
        assert counts(row) == expected, region

    assert cell(payload, "Galio", "MIDDLE", "SILVER_GOLD", "europe")["n_games"] == 1
    assert payload["excluded_unknown_position"] == 2
    assert payload["excluded_null_puuid"] == 2
    assert payload["patch"] == "16.16"
    assert payload["champions"] == {"Kaisa": 145, "Galio": 3, "Nilah": 895}
    assert payload["filters"]["min_duration_s"] == 1200
    assert "afk_proxy" in payload["filters"]


def test_cellules_vides_emises_et_ordre_deterministe(db, tmp_path):
    out = tmp_path / "out"
    assert run(db, out, "--patch", "16.16") == 0
    rows = load(out, "16.16")["rows"]

    # 3 champions × 5 postes × 4 buckets × (3 régions + ALL)
    assert len(rows) == 3 * 5 * 4 * 4
    nilah = [r for r in rows if r["champion"] == "Nilah"]
    assert nilah and all(counts(r) == (0, 0, 0, 0, 0) for r in nilah)

    # ALL en dernier de chaque groupe champion × poste × bucket
    for i in range(0, len(rows), 4):
        group = rows[i:i + 4]
        assert [r["region"] for r in group][-1] == "ALL"
        assert len({(r["champion"], r["team_position"], r["bucket"])
                    for r in group}) == 1


# --------------------------------------------------------------------------
# 2) Périmètre des compteurs : les lignes 3-5 ignorent les games sales
# --------------------------------------------------------------------------

def test_compteurs_ignorent_les_games_sales(db, tmp_path):
    out = tmp_path / "out"
    assert run(db, out, "--patch", "16.16") == 0
    row = cell(load(out, "16.16"), "Kaisa", "BOTTOM", "IRON_BRONZE", "europe")

    # deux games sales : n_games les compte, n_clean non
    assert row["n_games"] - row["n_clean"] == 2
    # elles ont toutes deux une timeline 'ok' : elle ne doit pas être comptée
    assert row["n_timeline"] == 4
    # pu_e2 a 3 games propres + 2 sales : même en les comptant il resterait
    # sous 5, mais la game AFK et la game courte ne doivent pas l'y amener
    assert row["n_puuid_ge5"] == 1


def test_une_game_sale_seule_ne_produit_aucun_joueur(tmp_path):
    path = tmp_path / "sales.db"
    conn = _new_db(path)
    add_game(conn, "EUW1_A", "europe", "IRON_BRONZE", "16.16", 900, "ok",
             [("pu_x", KAISA, "BOTTOM")])
    add_game(conn, "EUW1_B", "europe", "IRON_BRONZE", "16.16", 2400, "ok",
             [("pu_x", KAISA, "BOTTOM")], afk=True)
    conn.commit()
    conn.close()

    out = tmp_path / "out"
    assert run(path, out, "--patch", "16.16") == 0
    row = cell(load(out, "16.16"), "Kaisa", "BOTTOM", "IRON_BRONZE", "europe")
    assert counts(row) == (2, 0, 0, 0, 0)


# --------------------------------------------------------------------------
# 3) Ligne ALL = somme cohérente des trois régions
# --------------------------------------------------------------------------

def test_ligne_all_coherente(db, tmp_path):
    out = tmp_path / "out"
    assert run(db, out, "--patch", "16.16") == 0
    rows = load(out, "16.16")["rows"]

    par_groupe = {}
    for row in rows:
        key = (row["champion"], row["team_position"], row["bucket"])
        par_groupe.setdefault(key, {})[row["region"]] = row

    checked = 0
    for key, group in par_groupe.items():
        regions = [group[r] for r in ("europe", "asia", "americas")]
        allrow = group["ALL"]
        for field in ("n_games", "n_clean", "n_timeline"):
            assert allrow[field] == sum(r[field] for r in regions), (key, field)
        # les puuid sont dédupliqués : ALL ne peut pas dépasser la somme
        for field in ("n_puuid", "n_puuid_ge5"):
            assert allrow[field] <= sum(r[field] for r in regions), (key, field)
        if allrow["n_games"]:
            checked += 1
    assert checked >= 2, "le jeu de test doit avoir des groupes non vides"


# --------------------------------------------------------------------------
# 4) Détection de scan : arrêt code 2 AVANT exécution
# --------------------------------------------------------------------------

BAD_SQL = """
SELECT p.match_id
FROM participants p
JOIN matches m ON m.match_id = p.match_id
WHERE p.champion_name = ?
"""


def test_scan_detecte_avant_execution(db, capsys):
    conn = cc.open_db(str(db))
    executed = []
    conn.set_trace_callback(executed.append)
    conn.execute("SELECT 1")            # le traceur voit bien ce qui s'exécute
    assert executed == ["SELECT 1"], executed
    executed.clear()

    with pytest.raises(cc.ScanDetected) as exc:
        cc.check_plan(conn, BAD_SQL, ("Kaisa",), label="requête non indexée")
    assert exc.value.code == 2
    assert "SCAN p" in str(exc.value)

    # la requête fautive n'a jamais été exécutée
    assert executed == [], executed
    conn.close()


def test_plan_nominal_passe_par_les_index(db, capsys):
    conn = cc.open_db(str(db))
    plan = cc.check_plan(conn, cc.SQL_COVERAGE, (145, "16.16"), label="couverture")
    joined = " | ".join(plan)
    assert "SEARCH p USING INDEX idx_participants_champ_patch" in joined, joined
    assert "SEARCH p2 USING INDEX idx_participants_match" in joined, joined
    assert not any(line.startswith("SCAN") and " p" in f" {line}"
                   for line in plan), joined
    assert "EXPLAIN QUERY PLAN" in capsys.readouterr().out
    conn.close()


def test_index_absent_arrete_le_script(db, tmp_path):
    """Sans `idx_participants_champ_patch`, la requête de couverture scanne
    `participants` : le script doit sortir en 2 sans rien exécuter."""
    conn = sqlite3.connect(db)
    conn.execute("DROP INDEX idx_participants_champ_patch")
    conn.commit()
    conn.close()

    out = tmp_path / "out"
    assert run(db, out, "--patch", "16.16") == 2
    assert not (out / "coverage_16.16.json").exists()


# --------------------------------------------------------------------------
# 5) Tri des patchs : 16.9 < 16.16
# --------------------------------------------------------------------------

def test_tri_version_aware():
    patchs = ["16.9", "16.16", "16.2", "15.24", "16.10"]
    assert sorted(patchs, key=cc.patch_sort_key) == [
        "15.24", "16.2", "16.9", "16.10", "16.16"]
    # le tri lexical, lui, met "16.9" en dernier : c'est le bug à éviter
    assert sorted(patchs)[-1] == "16.9"


def test_patch_par_defaut_est_le_plus_recent(db, tmp_path):
    out = tmp_path / "out"
    assert run(db, out) == 0
    assert (out / "coverage_16.16.json").exists()
    assert not (out / "coverage_16.9.json").exists()


def test_all_patches_produit_un_json_par_patch(db, tmp_path):
    out = tmp_path / "out"
    assert run(db, out, "--all-patches") == 0
    assert (out / "coverage_16.16.json").exists()
    assert (out / "coverage_16.9.json").exists()


def test_patch_absent_code_1(db, tmp_path, capsys):
    out = tmp_path / "out"
    assert run(db, out, "--patch", "16.15") == 1
    assert "16.15" in capsys.readouterr().err


def test_patch_et_all_patches_exclusifs(db, tmp_path):
    out = tmp_path / "out"
    assert run(db, out, "--patch", "16.16", "--all-patches") == 1


def test_base_introuvable_code_1(tmp_path, capsys):
    assert run(tmp_path / "absente.db", tmp_path / "out") == 1
    assert "absente.db" in capsys.readouterr().err


# --------------------------------------------------------------------------
# 6) Garde-fou champion
# --------------------------------------------------------------------------

def test_garde_fou_champion_code_1(tmp_path, capsys):
    path = tmp_path / "mauvais_nom.db"
    conn = _new_db(path)
    add_game(conn, "EUW1_1", "europe", "IRON_BRONZE", "16.16", 1800, "ok",
             [("pu_1", ("KaiSa", 145), "BOTTOM")])
    conn.commit()
    conn.close()

    out = tmp_path / "out"
    assert run(path, out, "--patch", "16.16") == 1
    err = capsys.readouterr().err
    assert "145" in err and "KaiSa" in err
    assert not (out / "coverage_16.16.json").exists()


def test_champion_absent_de_la_base_ne_bloque_pas(db, tmp_path):
    """Nilah n'a aucune ligne dans la fixture : rien à contredire, le script
    doit tourner et émettre ses cellules à zéro."""
    out = tmp_path / "out"
    assert run(db, out, "--patch", "16.16") == 0
    assert any(r["champion"] == "Nilah" for r in load(out, "16.16")["rows"])


# --------------------------------------------------------------------------
# 7) Lecture seule
# --------------------------------------------------------------------------

def test_connexion_en_lecture_seule(db):
    conn = cc.open_db(str(db))
    assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO timeline_state (match_id, status)"
                     " VALUES ('X', 'ok')")
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("CREATE TABLE t (a)")
    conn.close()


def test_le_script_ne_modifie_pas_la_base(db, tmp_path):
    before = db.read_bytes()
    assert run(db, tmp_path / "out", "--all-patches") == 0
    assert db.read_bytes() == before
