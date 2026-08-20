# Spec — Lot 1 : export benchmarks (`export --study benchmarks`)

Statut : à faire · Dépend de : Lot 0 (livré), Lot 14 · PR unique
Rédigée le 2026-08-14 d'après le schéma réel, `out/coverage/coverage_16.15.json`
et les décisions actées au brief §5.1–5.4 (13 août 2026). C'est le socle commun
du §1 du brief : un seul calcul, deux consommateurs (Études et Coach).

## 1. Objectif

`python3 collector.py export --study benchmarks [--patch X.Y] [--out <dir>]`

Produit, pour un patch, les références du module Coach :

- **Niveau 1 — par joueur** (8 métriques sur `participants`) : P90 orienté et
  médiane du bucket supérieur, par cellule.
- **Niveau 2 — par game** (4 métriques Timeline) : P90 orienté et P50, par
  cellule, sur les games échantillonnées.
- **Répartition des postes** par champion (étage 3 de dégradation, §5.3).

Sortie : `out/benchmarks/<patch-slug>/` — `benchmarks.json`,
`benchmarks-timeline.json`, `roles.json`, `meta.json`.

L'export **ne décide pas** de l'étage servi : il publie toutes les cellules
avec leurs effectifs et leurs flags de couverture ; le consommateur (endpoint
du Lot 9) applique la dégradation. Réutiliser l'infrastructure de
`lolcollector/export.py` : `wilson_ci`, `_assert_covering_plan`,
`ensure_export_indexes`, `detect_patch_and_version`, `fetch_champion_names`,
`assert_destination_matches`, `_write_json`, conventions de `meta.json`.

## 2. Contraintes absolues

1. Le collecteur tourne pendant l'export (précédent : export tierlist).
   Lecture seule sur la base, requêtes courtes, autocommit.
2. **Aucun scan de `participants`** : chaque requête passe par
   `_assert_covering_plan`. Chemins autorisés : `idx_participants_champ_patch`
   (point d'entrée), `idx_participants_match`, clés primaires de `matches` et
   `timeline_state`, `idx_tl_events_match_type`, `idx_tl_frames_match_minute`,
   `idx_item_events_match`. La liste des champions vient de Data Dragon
   (`fetch_champion_names`), jamais d'un `DISTINCT` sur `participants`.
3. Aucun nom de colonne deviné : schéma de référence = `lolcollector/db.py`
   (post-Lots 13 et 14).
4. **Déterminisme byte-à-byte** : deux exécutions sur la même base produisent
   des JSON identiques (tris explicites, arrondis fixés, aucun aléa — les IC
   de percentiles utilisent la méthode des statistiques d'ordre, pas de
   bootstrap).

## 3. Population et cellules

- **Game propre** : définition du Lot 0 (`game_duration ≥ 1200` + proxy AFK
  `kills=0 AND assists=0 AND total_cs=0`), reprise à l'identique pour la
  comparabilité avec la mesure de couverture ; rappelée dans `meta.json`.
- **Cellule** : `champion × team_position × bucket × région`, plus l'étage
  agrégé `region = "ALL"`. `team_position` hors `TOP|JUNGLE|MIDDLE|BOTTOM|
  UTILITY` exclu et compté (`excluded_unknown_position`), `puuid` NULL exclu
  et compté (`excluded_null_puuid`) — mêmes conventions que le Lot 0.
- Cellules à zéro : émises quand même (information d'architecture).
- Accès : une requête par champion × patch (forme du §5 de `specs/Lot 0.md`,
  enrichie des colonnes nécessaires), agrégation en Python.

## 4. Niveau 1 — référence par joueur (`benchmarks.json`)

### 4.1 Population
Joueurs (`puuid`) ayant **≥ 5 games propres dans la cellule**. Couverture de
la cellule : `n_puuid_ge5 ≥ 30` (§5.3 du brief) → flag `covered`.

### 4.2 Métriques
Valeur joueur = moyenne sur ses games propres de la cellule (winrate =
proportion de victoires). Colonnes réelles, direction du « mieux » explicite :

| clé | formule (par game, puis moyenne joueur) | mieux |
|---|---|---|
| `cs_per_min` | `total_cs / (game_duration/60)` | haut |
| `cs_at10` | `lane_cs_at10 + jungle_cs_at10` | haut |
| `deaths_per_game` | `deaths` | bas |
| `damage_per_min` | `damage_to_champions / (game_duration/60)` | haut |
| `gold_per_min` | `gold_earned / (game_duration/60)` | haut |
| `kill_participation` | `(kills+assists) / team_kills` | haut |
| `vision_score` | `vision_score` | haut |
| `winrate` | `win` | haut |

- `team_kills` = somme des `kills` des 5 participants de la même équipe du
  match (sous-requête par `idx_participants_match`) ; game à 0 kill d'équipe
  exclue de cette métrique.
- Colonnes Lot 13 NULL (matchs pré-migration) : la game est exclue **de cette
  métrique seulement**. Conséquence assumée : sur 16.15/16.16, les effectifs
  varient par métrique → publier `n_players` (et `n_games`) **par métrique**,
  pas par cellule. Un joueur reste éligible à une métrique s'il conserve
  ≥ 5 games renseignées pour elle.

### 4.3 Statistiques publiées, par cellule × métrique
- `p90` : percentile 90 **orienté** (si `mieux = bas`, c'est le percentile 10
  arithmétique ; la clé reste `p90`, le champ `direction` documente le sens).
  Interpolation linéaire, définition unique documentée dans `meta.json`.
- `p90_ci` : IC 95 % distribution-free par statistiques d'ordre (bornes =
  valeurs de rang encadrant, rangs par la binomiale ; déterministe).
- `median` : P50 de la cellule.
- `upper_median` : P50 de la même métrique dans le **bucket supérieur**
  (ordre `IRON_BRONZE < SILVER_GOLD < PLAT_EMERALD < DIAMOND_PLUS`, même
  champion × poste × région-étage) ; `null` pour `DIAMOND_PLUS`, et si le
  bucket supérieur n'est pas couvert, `upper_median_covered = false`
  (le consommateur dégrade ou masque la 3e colonne, §5.2).
- `winrate` : IC Wilson 95 % (`wilson_ci`) sur l'agrégat de games, en plus du
  P90 par joueur.
- `n_players`, `n_games`.

## 5. Niveau 2 — référence par game (`benchmarks-timeline.json`)

### 5.1 Population et seuil
Games **propres avec timeline** (`timeline_state.status='ok'`) de la cellule.
Couverture : **`n_timeline_clean ≥ 200`** (P90 = 20e valeur ; brief §5.4).
L'unité statistique est la game, pas le joueur : champ `unit = "game"` sur
chaque ligne, l'asymétrie avec le Niveau 1 est portée jusqu'à l'UI (décision
actée). Le joueur de la cellule est relié à la timeline par
`participants.participant_id` (Lot 14).

### 5.2 Métriques (valeur par game)

| clé | définition | mieux |
|---|---|---|
| `deaths_pre5` | nb d'événements `CHAMPION_KILL` avec `victim_id = participant_id` du joueur et `timestamp_ms < 300000` | bas |
| `gold_diff_at15` | `total_gold(joueur, minute 15) − total_gold(vis-à-vis, minute 15)` ; vis-à-vis = participant du même match, équipe adverse, même `team_position` | haut |
| `nearest_ally_at_death` | pour chaque mort du joueur : distance euclidienne entre la position de l'événement et la position du plus proche allié (hors victime) à la frame dont la minute est la plus proche du timestamp ; valeur game = médiane de ses morts | bas |
| `deaths_3plus_assists_share` | part des morts du joueur (`CHAMPION_KILL`, `victim_id` = joueur) dont `assisting_ids` compte ≥ 3 ids (mort en teamfight vs duel, §5.5 du brief) | diagnostic |

`assisting_ids` : NULL = timeline antérieure au Lot 14 → game exclue de
`deaths_3plus_assists_share` seulement ; `''` = kill sans assistant, compté
comme 0 assistant. Ne jamais confondre les deux.

Exclusions comptées par métrique (`n_excluded` + raison dans `meta.json`) :
game < 15 min ou frame 15 absente ; vis-à-vis introuvable ou multiple ;
game sans mort du joueur (pour `nearest_ally_at_death` et
`deaths_3plus_assists_share`) ; `assisting_ids` NULL (pré-Lot 14).
**Approximation documentée** : les positions alliées sont à la frame-minute
(± 30 s de la mort). `meta.json` porte
`approximations.nearest_ally_at_death`, et le consommateur doit l'afficher.

### 5.3 Statistiques publiées
`p90` orienté, `p90_ci` (statistiques d'ordre), `median`, `upper_median`
(bucket supérieur, mêmes règles qu'au §4.3), `n_games`, `covered`
(`n_timeline_clean ≥ 200`).

Cas `direction: "diagnostic"` (`deaths_3plus_assists_share`) : aucune des deux
queues n'est « mieux » — mourir en teamfight et mourir isolé sont deux
diagnostics distincts (§5.5 du brief). Publier `p10`, `median`, `p90`
arithmétiques (non orientés) avec leurs IC d'ordre ; le moteur de conseils
(Lot 11) interprète les queues, pas l'export.

## 6. Répartition des postes (`roles.json`) — étage 3

Par `champion × bucket × région` (+ `ALL`) : `n_clean` par `team_position`,
issus de la même passe de lecture. C'est la donnée que l'endpoint affiche
quand aucun étage n'est couvert pour le poste demandé (§5.3 du brief) — les
faits seuls, aucune conclusion de viabilité.

## 7. `meta.json`

Conventions de l'export tierlist, plus : `study: "benchmarks"`, patch, date,
filtres (repris du Lot 0), seuils (`min_players_per_cell: 30`,
`min_timeline_games_per_cell: 200`), définition du percentile et des IC,
`unit` par fichier, approximations, versions (Data Dragon), et les compteurs
d'exclusion globaux. Tout chiffre publié par les Études ou le Coach doit être
traçable jusqu'à ces fichiers (contrainte n°2 du brief).

## 8. Tests — `tests/test_benchmarks.py`

Base fixture construite dans `tmp_path` (DDL + index réels, y compris Lot 14).
Cas minimum :

1. **Valeurs exactes calculées à la main** : fixture où P90, P90 orienté
   (métrique « bas »), médiane, `upper_median` et Wilson sont vérifiables à la
   main → JSON conforme.
2. Population Niveau 1 : joueur à 4 games propres exclu ; games sales
   exclues ; NULL Lot 13 → exclusion par métrique seulement, effectifs par
   métrique corrects.
3. `kill_participation` : `team_kills` correct, game à 0 kill d'équipe exclue.
4. Niveau 2 : `deaths_pre5` (borne 300 000 ms stricte), `gold_diff_at15`
   (vis-à-vis correct ; vis-à-vis manquant → exclusion comptée),
   `nearest_ally_at_death` (frame la plus proche, plus proche allié, médiane),
   `deaths_3plus_assists_share` (`''` compté 0 assistant ; NULL → game exclue
   de cette métrique seulement et comptée ; p10/median/p90 non orientés).
5. Seuils et flags : cellule à 29 joueurs → `covered=false` mais publiée ;
   199 games-timeline → idem au Niveau 2.
6. `upper_median` : bucket supérieur non couvert → `upper_median_covered=false` ;
   `DIAMOND_PLUS` → `null`.
7. **Déterminisme** : deux exécutions → fichiers identiques octet par octet.
8. Détection de scan : requête volontairement non indexée → `ExportError`
   avant exécution (réutilise le mécanisme existant).

## 9. Critère de succès du lot

- `pytest tests/test_benchmarks.py` vert + suite existante verte ;
- export sur la base réelle (patch courant) : code 0, les 4 fichiers produits,
  tous les EXPLAIN sans `SCAN` de `participants`, collecteur non perturbé ;
- rejouer l'export → fichiers identiques (déterminisme constaté en réel).
