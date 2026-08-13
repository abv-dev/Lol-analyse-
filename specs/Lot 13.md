# Spec — Lot 13 : extension du collecteur (dégâts, vision, métriques de fin de partie)

Statut : à faire · **Même rang d'urgence que le Lot 0, lançable en parallèle** · PR unique
Rédigée le 2026-08-12 d'après le code réel du collecteur (`lolcollector/db.py`,
`stop.sh`, `start.sh`, `lolcollector/refresh.py`).

## 1. Pourquoi ce lot est urgent et non conditionnel

`participants` n'a aucune colonne de dégâts ni de vision score. Les dégâts/min
portent les croisements de diagnostic du §5.5 du brief (« dégâts bas + peu de
morts = passivité », « dégâts bas + beaucoup de morts = placement ») : sans
eux, le Coach perd son diagnostic principal. Le collecteur ne conserve pas le
JSON brut Match-V5 : **aucun backfill n'est possible**, chaque match collecté
sans ces colonnes est définitivement perdu pour ces métriques (~157 000
matchs/jour). C'est le seul lot du plan dont le coût de report est
irréversible ; il ne dépend de rien et n'attend pas la lecture de
`out/coverage/`.

Précédent dans la base : `team_objectives.horde_kills`, ajouté après coup par
le mécanisme `MIGRATIONS`, NULL sur tous les matchs antérieurs. Ce lot suit
exactement le même chemin.

## 2. Mécanique existante (constatée dans le code, rien à inventer)

- `lolcollector/db.py` contient un `SCHEMA` (`CREATE TABLE IF NOT EXISTS` +
  index) exécuté à chaque construction de `Database`, puis `_migrate()` qui
  parcourt la liste `MIGRATIONS = [(table, colonne, "ALTER TABLE … ADD COLUMN …")]`
  et n'applique l'`ALTER` que si la colonne est absente de
  `PRAGMA table_info(table)`. **C'est déjà idempotent** ; le lot ajoute des
  entrées, il ne crée pas de nouveau mécanisme.
- `SQLite : ALTER TABLE … ADD COLUMN` est un changement de schéma pur — pas de
  réécriture de table, exécution quasi instantanée même sur ~22 M de lignes,
  à condition (respectée ici) de ne pas poser de défaut non constant. Les
  lignes existantes lisent NULL.
- L'insertion des participants se fait dans `Database.store_match()` avec une
  liste de colonnes explicite — l'ajout est additif, aucun `SELECT *` à
  protéger.
- L'arrêt/redémarrage encadré existe : `stop.sh` (SIGTERM, attente gracieuse
  30 s, `kill -9` en dernier recours) et `start.sh` (relance `nohup`,
  vérifie le pid). `refresh` encadre ce même cycle mais déclenche un export —
  inutile ici : on passe par `stop.sh`/`start.sh` directement.

## 3. Colonnes ajoutées

Convention de nommage existante : snake_case calqué sur le champ Riot
(`gold_earned` ← `goldEarned`). Tous les champs proviennent de
`info.participants[]` du JSON Match-V5, sauf mention contraire. Type
`INTEGER` partout.

### 3.1 `participants` — 10 colonnes

| Colonne | Champ Match-V5 | Justification |
|---|---|---|
| `damage_to_champions` | `totalDamageDealtToChampions` | Le cœur du lot : porte les deux croisements principaux du §5.5. Dégâts/min = colonne ÷ `matches.game_duration`. |
| `damage_taken` | `totalDamageTaken` | Complète morts/game pour le diagnostic de placement : mourir beaucoup en encaissant peu (pris/one-shot) et mourir en encaissant beaucoup (frontline) sont deux profils différents que `deaths` seul confond. |
| `vision_score` | `visionScore` | Métrique Niveau 1 actée au §5.4 du brief. |
| `control_wards_bought` | `visionWardsBoughtInGame` | Rend le conseil vision falsifiable : « 0,4 pink/game → 2 par game sur 3 games » se vérifie ; « ward plus » ne vaut rien (règle du §5.6). |
| `time_spent_dead` | `totalTimeSpentDead` (secondes) | Deux joueurs à 6 morts ne paient pas le même prix selon le moment des morts ; c'est le coût en tempo, non dérivable de `deaths`. |
| `lane_cs_at10` | `challenges.laneMinionsFirst10Minutes` | Rapatrie « CS à 10 min » (§5.4) en Niveau 1 à couverture 100 %, au lieu de dépendre des timelines échantillonnées à 0,10. |
| `jungle_cs_at10` | `challenges.jungleCsBefore10Minutes` | Même métrique pour la cellule JUNGLE, où les sbires de lane ne mesurent rien. |
| `turret_plates_taken` | `challenges.turretPlatesTaken` | Pression de lane avant 14 min, non dérivable ailleurs (les événements de plates n'existent que dans les timelines à 0,10). Conseil falsifiable pour TOP/MIDDLE/BOTTOM. |
| `heals_on_teammates` | `totalHealsOnTeammates` | Sans elles, la cellule UTILITY n'a que vision et morts : aucune métrique de production. Ce sont les deux sorties standard d'un support. |
| `shields_on_teammates` | `totalDamageShieldedOnTeammates` | Idem — enchanteurs à boucliers vs soigneurs, les deux moitiés du rôle. |

### 3.2 `matches` — 1 colonne

| Colonne | Champ Match-V5 | Justification |
|---|---|---|
| `early_surrender` | `gameEndedInEarlySurrender` (pris sur `info.participants[0]`, identique pour les 10 ; stocké 0/1) | Rend exact, pour les matchs futurs, le filtre « pas d'early surrender » de la définition de game propre — aujourd'hui approximé par la seule durée. Valeur par match, donc sur `matches`, pas dupliquée ×10 sur `participants`. |

### 3.3 Champs écartés, et pourquoi

- `challenges.killParticipation`, `challenges.goldPerMinute`,
  `challenges.damagePerMinute` : dérivables des colonnes stockées (jointure
  d'équipe par `match_id`, division par la durée). On ne stocke pas de
  dérivé — deux sources pour un même chiffre, c'est le risque de divergence
  que le socle commun existe précisément pour éviter.
- `wardsPlaced`, `wardsKilled` : `vision_score` + `control_wards_bought`
  suffisent aux barèmes et aux conseils falsifiables ; inflation de colonnes
  sans croisement acté qui les consomme.
- `damageDealtToObjectives` : la prise d'objectifs est déjà couverte au
  niveau équipe par `team_objectives` ; aucun croisement acté n'utilise le
  signal individuel.
- `gameEndedInSurrender` (reddition normale) : une game rendue après 20 min
  est une game propre valide ; le champ ne filtre rien.

L'argument d'irréversibilité ne justifie pas de tout stocker : chaque colonne
ajoutée est une surface de test et de migration permanente. La ligne de
partage : le champ alimente un croisement du §5.5, une métrique actée au
§5.4, ou une sortie de rôle sans laquelle une cellule n'a pas de benchmark.

## 4. Sémantique NULL — règle stricte

- **NULL = non collecté** (match antérieur à la migration, ou champ absent du
  payload). **0 = mesuré nul.** Ne jamais confondre les deux.
- À l'insertion : `part.get("totalDamageDealtToChampions")` → NULL si absent ;
  champs `challenges` via `(part.get("challenges") or {}).get(...)` → NULL si
  l'objet `challenges` manque (cas réel sur certains matchs). **Interdiction
  de recopier le style du helper `o()`** de `store_match`, qui coalesce à 0 —
  correct pour des compteurs d'objectifs, faux ici.
- Tous les matchs antérieurs à la migration restent NULL pour toujours, comme
  `horde_kills`. Les consommateurs (Lot 1 en tête) filtrent `IS NOT NULL` et
  peuvent borner par `matches.inserted_at >= <borne>` (index
  `idx_matches_inserted`), la borne étant fournie par le tampon méta ci-dessous.

## 5. Modifications de code (périmètre exact de la PR)

1. `SCHEMA` de `db.py` : ajouter les 11 colonnes aux `CREATE TABLE IF NOT
   EXISTS` de `participants` et `matches` (installations neuves), avec un
   commentaire par groupe indiquant l'origine Match-V5 — précédent
   `herald_kills`/`horde_kills`.
2. `MIGRATIONS` : ajouter 11 entrées
   `("participants", "damage_to_champions", "ALTER TABLE participants ADD COLUMN damage_to_champions INTEGER")`,
   etc. (bases existantes). Aucune modification de `_migrate()` nécessaire
   pour l'idempotence — elle existe.
3. Tampon de borne : lors de l'application **effective** de la première de ces
   migrations (colonne absente → `ALTER` exécuté), écrire une seule fois
   `meta['lot13_migrated_at'] = <epoch secondes>` ; ne jamais réécrire la clé
   si elle existe. C'est la borne temporelle des NULL, sans scan.
4. `store_match()` : étendre la liste de colonnes et le tuple de valeurs de
   l'INSERT `participants` (10 valeurs), et l'INSERT `matches`
   (`early_surrender`, 0/1 depuis le premier participant, NULL si champ
   absent). Respecter le style de parsing existant (`part.get(...)`).
5. Rien d'autre : pas de nouvel index (aucune requête ne filtre sur ces
   colonnes ; elles sont projetées via les index existants), pas de
   changement d'export, pas de refactoring opportuniste.

Coût de stockage estimé : 10 entiers ×10 lignes/match (+1 sur `matches`),
valeurs ≤ ~200 000 → ~30 o/ligne en varint SQLite, ≈ 45–50 Mo/jour
additionnels au rythme actuel. Compatible avec les ~25 Go libres et le
mécanisme `prune` existant. À mentionner dans la PR.

## 6. Procédure d'application en production — encadrée, pas improvisée

Le collecteur tourne en permanence ; la migration s'applique dans
`Database.__init__` au prochain démarrage. Le cycle encadré est
`stop.sh`/`start.sh` (le même que `refresh` utilise ; `refresh` lui-même est
inutile ici, il déclencherait un export sans objet).

```
cd ~/lol-studies-collector
git pull                  # (1) le code nouveau arrive, le collecteur ancien tourne encore : sans effet sur lui
./stop.sh                 # (2) SIGTERM, arrêt gracieux ≤ 30 s
./start.sh                # (3) Database.__init__ exécute SCHEMA + _migrate() : 11 ALTER quasi instantanés, puis les workers repartent
```

**Interdit entre (1) et (3)** : lancer toute autre commande construisant
`Database` (`collector.py export`, `stats`, `refresh`…) avec le nouveau code
pendant que le collecteur ancien tourne encore — elle appliquerait les `ALTER`
sur la base vivante et provoquerait des « database is locked » côté workers
(la raison même pour laquelle `refresh` arrête le collecteur avant d'exporter).

Vérifications post-redémarrage (lecture seule) :

1. `collector.pid` présent et processus vivant ; `logs/collector.log` sans
   erreur au démarrage.
2. Les 11 colonnes présentes :
   `PRAGMA table_info(participants)` / `PRAGMA table_info(matches)` via une
   connexion `mode=ro`.
3. `meta['lot13_migrated_at']` renseigné.
4. Après quelques minutes de collecte : la dernière ligne insérée
   (`SELECT damage_to_champions, vision_score FROM participants WHERE rowid =
   (SELECT max(rowid) FROM participants)`, O(1)) porte des valeurs non NULL.

Coût du cycle : l'arrêt dure ~30 s au pire, soit ~50–100 matchs non collectés
sur un débit de ~6 700/h — négligeable et non récurrent. Un collecteur laissé
à l'arrêt, lui, perd de la donnée définitivement : si `start.sh` échoue,
c'est l'incident prioritaire, on restaure le service avant tout diagnostic de
la migration (invariant hérité de `refresh`).

## 7. Fixture de test — un payload réel, pas un payload rédigé

Les noms de champs Riot du §3 doivent être **vérifiés contre un payload
réel**, pas crus sur parole (transposition de la contrainte « aucun nom de
colonne deviné » au JSON Match-V5, qui n'est pas conservé en base).

- Capturer **une** réponse Match-V5 complète avec la clé existante (une seule
  requête GET, budget négligeable), la committer en
  `tests/fixtures/match_v5_full.json`.
- Anonymisation autorisée : remplacer `puuid`, `riotIdGameName`,
  `riotIdTagline`, `summonerName` par des valeurs synthétiques de même forme.
  Interdiction de toucher aux noms de champs ou à la structure.
- Un test doit échouer si un champ attendu du §3 est absent de la fixture :
  c'est le garde-fou contre un renommage silencieux côté Riot ou une faute de
  frappe dans la spec.

## 8. Tests — `tests/test_collector_schema.py`

Sur base fixture dans `tmp_path`, jamais sur la production. Cas minimum :

1. **Migration** : créer une base à l'**ancien** schéma (DDL sans les 11
   colonnes), construire `Database` → colonnes présentes, lignes
   préexistantes à NULL, `lot13_migrated_at` renseigné.
2. **Idempotence** : construire `Database` une seconde fois → aucune erreur,
   `lot13_migrated_at` inchangé.
3. **Base neuve** : `Database` sur chemin vierge → les 11 colonnes existent
   dès le `SCHEMA`.
4. **Parsing** : `store_match(fixture)` → chaque nouvelle colonne égale au
   champ correspondant du payload (valeurs lues dans la fixture, pas
   codées en dur) ; `matches.early_surrender` cohérent avec le payload.
5. **NULL, pas 0** : payload copié puis privé de `challenges` (et d'un champ
   direct comme `visionScore`) → colonnes correspondantes à NULL.
6. **Garde-fou fixture** (§7) : présence de tous les champs attendus dans le
   payload committé.
7. **Non-régression** : la suite existante (`test_export_guards`,
   `test_prune`, `test_ratelimit`, `test_timeline`, `test_notify_discord`)
   reste verte, aucun test existant modifié (contrainte n°7 du brief).

## 9. Critère de succès du lot

- `pytest` vert sur `tests/test_collector_schema.py` **et** sur la suite
  existante intacte ;
- en production, procédure du §6 déroulée : collecteur redémarré, les 4
  vérifications passent, et les nouvelles lignes portent des valeurs non
  NULL pendant que les anciennes restent NULL — précédent `horde_kills`
  reproduit à l'identique.

## 10. Conséquences en aval (pour mémoire, hors lot)

- **Lot 1 (benchmarks)** : percentiles dégâts/vision calculables uniquement
  sur `inserted_at >= lot13_migrated_at`, `IS NOT NULL` obligatoire ; compter
  environ un patch de collecte avant d'avoir du volume.
- **Lot 0** : inchangé — son proxy AFK et son filtre durée doivent
  fonctionner sur l'historique, qui restera sans dégâts. Le raffinement du
  filtre « game propre » via `damage_to_champions` et `early_surrender` sur
  les matchs récents se décidera à la spec du Lot 1.
- Chaque jour entre la validation de cette spec et son déploiement coûte
  ~157 000 matchs sans ces métriques. Ce lot se planifie en premier.
