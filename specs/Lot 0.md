# Spec — Lot 0 : mesure de couverture (`scripts/coverage_check.py`)

Statut : à faire · Bloquant pour tout le module Coach · PR unique
Rédigée le 2026-08-12 d'après le schéma réel de `data/matches.db` (lu en
lecture seule pendant que le collecteur tournait — c'est le mode d'emploi
normal de ce script).

## 1. Objectif

Mesurer, cellule par cellule, les effectifs réellement disponibles pour le
module Coach, afin de fixer **après coup** : le seuil de validité d'une
cellule, la règle de dégradation, et la liste définitive des métriques
(Niveau 1 vs Timeline). Le script ne décide rien : il compte et publie.

Livrable : `scripts/coverage_check.py`, **lecture seule**, stdlib Python
uniquement (`sqlite3`, `argparse`, `json`, `datetime`, `pathlib`). Aucune
dépendance à `lolcollector` (le script doit tourner même si le paquet évolue).

## 2. Contraintes absolues

1. **Le collecteur tourne pendant l'exécution.** Interdits : tout arrêt de
   processus, toute écriture dans `data/matches.db`, `ATTACH`, `CREATE`
   (même `TEMP` sur la base cible), toute ouverture en lecture-écriture.
2. Ouverture obligatoire :
   `sqlite3.connect("file:data/matches.db?mode=ro", uri=True)`, puis
   `PRAGMA query_only=ON;` et `PRAGMA busy_timeout=5000;`.
3. **Aucune requête ne scanne `participants`** (22 M lignes). Chaque requête
   est précédée d'un `EXPLAIN QUERY PLAN` affiché sur la console ; si une
   ligne du plan commence par `SCAN` et référence `participants` ou l'un de
   ses alias (imposer des alias explicites `p`, `p2` et détecter
   `SCAN p`, `SCAN p2`, `SCAN participants`), le script s'arrête **avant
   d'exécuter la requête**, code de sortie 2.
4. Aucun nom de colonne deviné : le schéma de référence est au §3, relevé
   sur la base réelle.

## 3. Schéma réel constaté (2026-08-12)

Colonnes utilisées :

- `matches(match_id PK, region, platform, patch, game_duration, tier_bucket_source)`
  — `game_duration` en **secondes** ; `region` ∈ `europe|asia|americas` ;
  `patch` au format `X.Y` texte (ex. `16.16`) ; buckets :
  `IRON_BRONZE`, `SILVER_GOLD`, `PLAT_EMERALD`, `DIAMOND_PLUS`.
- `participants(match_id, puuid, champion_id, champion_name, team_position,
  kills, assists, total_cs, patch)` — `team_position` ∈
  `TOP|JUNGLE|MIDDLE|BOTTOM|UTILITY` ; `puuid` nullable.
- `timeline_state(match_id PK, status)` — `status` ∈ `ok|skipped` ;
  `ok` ⇔ timeline chargée (≈ 10 % des matchs, conformément au sampling).

Index disponibles (les seuls chemins d'accès autorisés vers `participants`) :

- `idx_participants_champ_patch (champion_id, patch)` — **point d'entrée
  obligatoire** de toute requête de couverture ;
- `idx_participants_match (match_id)` — pour la sous-requête AFK (§5) ;
- `matches` se joint par sa clé primaire `match_id` ;
- `timeline_state` se joint par sa clé primaire `match_id`.

`champion_name` n'est **pas indexé** : interdiction de filtrer dessus.

Champions cibles, casse vérifiée en base :

| champion_name | champion_id |
|---|---|
| `Kaisa` | 145 |
| `Galio` | 3 |
| `Nilah` | 895 |

Constante dans le script : `CHAMPIONS = {"Kaisa": 145, "Galio": 3, "Nilah": 895}`.
Garde-fou au démarrage : pour chaque id, lire
`SELECT champion_name FROM participants WHERE champion_id=? LIMIT 1`
(indexé) et échouer (code 1) si le nom ne correspond pas à la constante.

## 4. Définition « game propre » — adaptée au schéma réel

Le brief demande : durée ≥ 20 min, pas d'early surrender, aucun participant à
0 dégât. Le schéma réel impose deux adaptations, à documenter dans le JSON de
sortie (champ `filters`) :

1. **Durée** : `matches.game_duration >= 1200` (secondes).
2. **Early surrender** : aucune colonne ne l'enregistre. Un early surrender
   (remake ou reddition anticipée) termine la partie avant 20 min par
   définition : le filtre de durée le couvre. Aucun filtre supplémentaire.
3. **« 0 dégât »** : `participants` n'a **aucune colonne de dégâts**. Proxy
   AFK déterministe : la game est exclue s'il existe un participant avec
   `kills = 0 AND assists = 0 AND total_cs = 0`. Sur une game de ≥ 20 min,
   ce profil est un non-joueur avec une quasi-certitude. Le proxy est nommé
   `afk_proxy` dans `filters` pour que les lots aval sachent exactement ce
   qui a été filtré.

**Game propre** = durée ≥ 1200 s **et** aucun participant AFK-proxy.
Les lignes 3, 4 et 5 du §6 se calculent **sur les games propres uniquement**
(ce sont elles qui alimenteront les percentiles).

## 5. Requêtes — plan d'accès imposé

Une requête par combinaison champion × patch (3 requêtes par patch), qui
rapporte les lignes brutes ; l'agrégation par cellule se fait en Python
(volume borné : quelques dizaines de milliers de lignes par champion × patch).

Forme imposée (le développeur peut ajuster les expressions, pas les chemins
d'accès) :

```sql
SELECT p.match_id, p.puuid, p.team_position,
       m.region, m.tier_bucket_source, m.game_duration,
       (ts.match_id IS NOT NULL) AS has_timeline,
       NOT EXISTS (
         SELECT 1 FROM participants p2
         WHERE p2.match_id = p.match_id
           AND p2.kills = 0 AND p2.assists = 0 AND p2.total_cs = 0
       ) AS no_afk
FROM participants p
JOIN matches m ON m.match_id = p.match_id
LEFT JOIN timeline_state ts
       ON ts.match_id = p.match_id AND ts.status = 'ok'
WHERE p.champion_id = ? AND p.patch = ?
```

Plan attendu : `SEARCH p USING INDEX idx_participants_champ_patch`,
`SEARCH m USING PRIMARY KEY` (ou index couvrant), `SEARCH ts USING PRIMARY
KEY`, `SEARCH p2 USING INDEX idx_participants_match`. Tout `SCAN` sur `p`,
`p2` ou `participants` → arrêt code 2 (§2.3). Les requêtes annexes (liste
des patchs via `SELECT DISTINCT patch FROM matches`, garde-fou champion)
passent par les index de `matches` et `idx_participants_champ_patch` ; le
même contrôle d'EXPLAIN s'applique.

Hygiène vis-à-vis du collecteur : une requête par champion × patch, jamais
de requête embrassant toute la table ; pas de transaction longue (autocommit).

## 6. Cellules et compteurs

Cellule : `champion × team_position × bucket × région`, plus une ligne
agrégée `region = "ALL"` par `champion × team_position × bucket`.

- Champions : les 3 de la constante.
- `team_position` : les 5 valeurs du §3. Une ligne avec `team_position` NULL,
  vide ou hors liste est exclue des cellules et comptée dans un champ global
  `excluded_unknown_position` (transparence).
- Buckets : les 4 valeurs du §3 (constante, pas de découverte dynamique).
- Régions : `europe`, `asia`, `americas`, `ALL`.
- Les cellules à 0 game sont émises quand même (un zéro est une information
  d'architecture, pas du bruit).

Compteurs par cellule, dans cet ordre :

1. `n_games` — nombre de games (toutes, avant filtres).
2. `n_clean` — games propres (§4).
3. `n_puuid` — `puuid` distincts sur les games propres (`puuid` NULL exclus
   et comptés dans un champ global `excluded_null_puuid`).
4. `n_puuid_ge5` — `puuid` distincts avec **≥ 5 games propres dans la
   cellule**. C'est le vrai plafond architectural.
5. `n_timeline` — games propres avec `timeline_state.status = 'ok'`.

## 7. Interface en ligne de commande

```
python scripts/coverage_check.py [--patch 16.16 | --all-patches]
                                 [--db data/matches.db] [--out out/coverage]
```

- Sans option : patch le plus récent présent dans `matches`. **Tri
  version-aware obligatoire** (`(16, 9) < (16, 16)`) : le tri lexical est
  faux (`"16.9" > "16.16"`), c'est un bug classique à tester.
- `--patch X.Y` : ce patch uniquement ; erreur claire (code 1) si absent.
- `--all-patches` : itère tous les patchs présents, un JSON par patch.
- `--patch` et `--all-patches` mutuellement exclusifs.

## 8. Sorties

### Console
Par patch : pour chaque champion, un tableau aligné lisible
(`position | bucket | région | games | propres | joueurs | joueurs≥5 | timelines`),
précédé des `EXPLAIN QUERY PLAN`. En fin d'exécution, un rappel des deux
lectures décisives : plus petit `n_puuid_ge5` non nul et taux de couverture
timeline par cellule.

### JSON — `out/coverage/coverage_<patch>.json`
Le répertoire est créé si absent. Écrasement silencieux autorisé (le script
est rejouable, la base bouge en permanence). Schéma :

```json
{
  "generated_at": "2026-08-12T18:00:00+00:00",
  "db_path": "data/matches.db",
  "patch": "16.16",
  "filters": {
    "min_duration_s": 1200,
    "early_surrender": "couvert par min_duration_s (aucune colonne dédiée)",
    "afk_proxy": "exclusion si un participant a kills=0 AND assists=0 AND total_cs=0",
    "clean_scope": "les compteurs 3-5 portent sur les games propres"
  },
  "champions": {"Kaisa": 145, "Galio": 3, "Nilah": 895},
  "excluded_unknown_position": 0,
  "excluded_null_puuid": 0,
  "rows": [
    {
      "champion": "Kaisa", "champion_id": 145,
      "team_position": "BOTTOM", "bucket": "IRON_BRONZE", "region": "europe",
      "n_games": 0, "n_clean": 0, "n_puuid": 0, "n_puuid_ge5": 0, "n_timeline": 0
    }
  ]
}
```

`rows` triées par (champion, team_position, bucket, région) avec `ALL` en
dernière position de chaque groupe — ordre déterministe pour des diffs lisibles.

### Codes de sortie
`0` succès · `1` erreur (patch absent, garde-fou champion, base introuvable) ·
`2` plan d'exécution avec `SCAN` de `participants` (aucune requête exécutée
au-delà du point de détection).

## 9. Tests — `tests/test_coverage_check.py`

Les tests tranchent (contrainte n°7 du brief) ; ils tournent sur une **base
fixture** construite dans `tmp_path` avec le DDL et les index du §3, jamais
sur la base de production. Cas minimum :

1. Comptage exact : fixture avec games propres/sales connues (une < 1200 s,
   une avec participant AFK-proxy, puuids répartis pour que `n_puuid_ge5`
   diffère de `n_puuid`, timelines `ok`/`skipped`) → JSON conforme aux
   valeurs attendues à la main.
2. Périmètre des compteurs : les lignes 3–5 ignorent les games sales.
3. Ligne `ALL` = somme cohérente des trois régions.
4. Détection de scan : une requête volontairement non indexée (par exemple
   filtrée sur `champion_name`) soumise à la fonction de contrôle du plan →
   code 2, et la requête n'est pas exécutée.
5. Tri des patchs : `16.9` < `16.16` (défaut = `16.16`).
6. Garde-fou champion : fixture avec `champion_name` inattendu pour l'id →
   code 1.
7. Lecture seule : la connexion refuse une écriture (`query_only` actif) —
   un `INSERT` tenté dans le test lève une exception.

## 10. Critère de succès du lot

- `pytest tests/test_coverage_check.py` vert ;
- `python scripts/coverage_check.py` (patch courant) sur la base réelle :
  code 0, `out/coverage/coverage_<patch>.json` produit, tous les EXPLAIN
  affichés sans `SCAN` de `participants` ;
- le collecteur n'a été ni arrêté ni ralenti (aucune écriture, requêtes
  courtes).

## 11. Ce que la lecture de `out/coverage/` doit trancher (hors lot)

1. Le **seuil de validité** d'une cellule (ordre de grandeur attendu :
   ≥ 30 joueurs éligibles, ligne `n_puuid_ge5`) et la règle de dégradation
   vers `champion × team_position` toutes régions.
2. La faisabilité des **métriques Timeline** à 0,10 de sampling
   (ligne `n_timeline`) : gardées, dégradées ou reportées. Le Lot 13 (acté,
   spécifié dans `specs/Lot 13.md`) rapatriera plusieurs d'entre elles en fin
   de partie pour les matchs futurs ; la lecture tranche donc surtout le sort
   des métriques qui resteront exclusivement Timeline (positions, morts
   détaillées, gold à 15 min).

Aucun lot aval (1, 8–12) ne se spécifie avant cette lecture.
