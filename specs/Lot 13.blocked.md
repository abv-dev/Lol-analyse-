# Lot 13 — bloqué : §5 et §8.7 sont mutuellement exclusifs

> **Levé le 2026-08-13, option A retenue** (commit « Tests : nomme les colonnes
> des INSERT sur matches et participants »). Nommer les colonnes n'affaiblit
> aucun test : aucune assertion ni valeur attendue ne change, la couverture est
> identique, seule la forme de l'insertion devient explicite — la règle que le
> code de production applique déjà. Document conservé pour la revue : il dit
> pourquoi trois fichiers de tests bougent dans une PR de schéma.

Statut à la rédaction : **aucun code écrit**, aucune migration appliquée,
collecteur non touché. Constaté le 2026-08-13 sur la branche
`lot-13/collector-damage-vision`.

## 1. Le blocage en une phrase

Le §5 demande d'ajouter 11 colonnes à `matches` et `participants` ; le §8.7
exige que la suite existante reste verte **sans modifier aucun test existant**.
Trois tests existants insèrent dans ces deux tables **sans liste de colonnes**
(`INSERT INTO matches VALUES (?,?,?,?,?,?,?,?,?)`) : en SQLite, un tel INSERT
exige exactement autant de valeurs que la table a de colonnes. Ajouter la
moindre colonne les casse. Les deux exigences ne peuvent pas être satisfaites
ensemble, et arbitrer entre elles revient à lever la contrainte n°7 du brief —
décision qui n'est pas la mienne.

## 2. Les cinq instructions concernées

| Fichier | Ligne | Instruction | Table |
|---|---|---|---|
| `tests/test_prune.py` | 42 | `INSERT OR IGNORE INTO matches VALUES (?×9)` | `matches` |
| `tests/test_export_guards.py` | 56 | `INSERT OR IGNORE INTO matches VALUES (?×9)` | `matches` |
| `tests/test_export_guards.py` | 59 | `INSERT INTO participants VALUES (?×23)` | `participants` |
| `tests/test_timeline.py` | 148 | `INSERT OR IGNORE INTO matches VALUES (?×9)` | `matches` |
| `tests/test_timeline.py` | 163 | `INSERT OR IGNORE INTO matches VALUES (?×9)` | `matches` |

Le code de production, lui, n'est pas concerné : `db.py` et `timeline.py`
listent toujours leurs colonnes explicitement (le §2 de la spec le dit — mais
il ne le vérifie que pour le code, pas pour les tests).

## 3. Preuve, pas déduction

Copie du dépôt hors arborescence de travail (`scratchpad/proof*`), base de
production jamais ouverte.

Référence — même trois fichiers, `db.py` intact :

```
$ python3 -m pytest tests/ -q
3 passed in 374.00s
```

Avec les 11 colonnes ajoutées au `SCHEMA` (§5.1) :

```
$ python3 -m pytest tests/test_prune.py tests/test_export_guards.py -q
E   sqlite3.OperationalError: table matches has 10 columns but 9 values were supplied  (test_prune.py:42)
E   sqlite3.OperationalError: table matches has 10 columns but 9 values were supplied  (test_export_guards.py:56)
$ python3 -m pytest tests/test_timeline.py -q
E   sqlite3.OperationalError: table matches has 10 columns but 9 values were supplied  (test_timeline.py:148)
```

Avec les 10 colonnes `participants` seules (`matches` laissée intacte) :

```
E   sqlite3.OperationalError: table participants has 33 columns but 23 values were supplied  (test_export_guards.py:58)
```

Les échecs surviennent **à la collecte** pytest : ces fichiers exécutent leurs
assertions au niveau module, un import qui lève interrompt tout le fichier.

## 4. Pourquoi il n'existe pas de contournement

- **Ne toucher que `MIGRATIONS`, pas `SCHEMA`** (§5.1 exclu, §5.2 seul) : ne
  change rien. `_migrate()` est appelé dans `Database.__init__`, donc sur
  **toute** base, y compris les bases neuves que ces tests créent en `tmp`.
  Vérifié :

  ```
  # MIGRATIONS += ("matches", "early_surrender", …), SCHEMA inchangé
  E   sqlite3.OperationalError: table matches has 10 columns but 9 values were supplied  (test_prune.py:42)
  ```

- **Ajouter les colonnes en fin de table** : déjà le cas (`ALTER TABLE … ADD
  COLUMN` ne peut pas faire autrement). Le problème n'est pas la position mais
  le nombre.
- **Valeur par défaut** : `DEFAULT 0` ne rendrait pas les valeurs facultatives
  dans un INSERT positionnel — et violerait le §4 (0 ≠ NULL) et le §2 (pas de
  défaut non constant sur ~22 M de lignes).
- **Vue de compatibilité** à l'ancienne forme : les tests écrivent sur la
  table, pas sur une vue ; il faudrait des triggers `INSTEAD OF` — une
  mécanique permanente inventée pour préserver cinq lignes de test, tout
  l'inverse du « rien d'autre » du §5.5.

## 5. Ce qu'il faut décider (deux options, aucune prise ici)

**Option A — lever la contrainte n°7 pour ces cinq lignes.** Nommer les
colonnes dans les cinq INSERT ci-dessus, sans toucher à une seule assertion ni
à un seul jeu de données. Exemple pour `tests/test_prune.py:42` :

```python
cur.execute("INSERT OR IGNORE INTO matches (match_id, region, platform,"
            " game_version, patch, game_duration, game_creation,"
            " tier_bucket_source, inserted_at) VALUES (?,?,?,?,?,?,?,?,?)",
```

C'est le changement minimal, il rend les tests robustes à tout ajout futur de
colonne (le problème se reposera au lot suivant qui en ajoute une), et il ne
modifie aucun comportement testé. Coût : ~5 lignes, 3 fichiers.

**Option B — renoncer aux colonnes.** Contredit le §1 (perte irréversible,
~157 000 matchs/jour). Mentionnée pour être complet, pas recommandée.

Ma lecture : l'option A est ce que la spec aurait écrit si elle avait regardé
les tests plutôt que le seul code de production (le §2 ne vérifie l'absence de
`SELECT *` que dans `db.py`). Mais le §8.7 cite explicitement la contrainte
n°7 du brief, donc la levée t'appartient.

**Dès que tu tranches, le reste du lot est prêt à écrire** : §3 à §5 sont sans
ambiguïté, y compris la règle NULL du §4 (`part.get(...)` et
`(part.get("challenges") or {}).get(...)`, jamais le helper `o()` qui coalesce
à 0) et le tampon `meta['lot13_migrated_at']` posé au premier `ALTER`
réellement appliqué.

## 6. Annexe — capture de la fixture `tests/fixtures/match_v5_full.json` (§7)

Indépendant du blocage ci-dessus : à lancer dans tous les cas. Une seule
requête GET, clé lue depuis `.env` et jamais affichée, match récent pris en
lecture seule dans la base (aucun `Database` construit, aucune écriture).

```bash
cd ~/lol-studies-collector && mkdir -p tests/fixtures && MATCH_ID=$(python3 -c "import sqlite3; print(sqlite3.connect('file:data/matches.db?mode=ro', uri=True).execute(\"SELECT match_id FROM matches WHERE region='europe' ORDER BY rowid DESC LIMIT 1\").fetchone()[0])") && curl -sS -H "X-Riot-Token: $(sed -n 's/^RIOT_API_KEY=//p' .env | tr -d '\r')" "https://europe.api.riotgames.com/lol/match/v5/matches/$MATCH_ID" -o tests/fixtures/match_v5_full.json && python3 -c "import json; d=json.load(open('tests/fixtures/match_v5_full.json')); p=d['info']['participants'][0]; print('queueId', d['info']['queueId'], '| participants', len(d['info']['participants']), '| challenges', 'challenges' in p)"
```

La dernière ligne doit afficher `queueId 420 | participants 10 | challenges
True`. Si `queueId` n'est pas 420, `store_match()` refusera la fixture ;
relancer en changeant `LIMIT 1` en `LIMIT 1 OFFSET n`.

Anonymisation ensuite (§7) : `puuid`, `riotIdGameName`, `riotIdTagline`,
`summonerName` remplacés par des valeurs synthétiques de même forme — aucun
nom de champ ni structure touchés.
