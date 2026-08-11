# lol-studies-collector

> Ce repo contient aussi **[EloLab](site/README.md)** (`site/`), le site
> Next.js qui publie les études produites à partir de ce dataset.

Collecteur continu de matchs **ranked solo/duo (queue 420)** via l'API Riot,
multi-régions, vers une base SQLite, pour des études pick/ban/winrate par
rank et région.

## Architecture

- **3 workers asyncio indépendants**, un par routing régional :
  `europe` (échantillonne euw1), `asia` (kr), `americas` (na1).
- Chaque worker a **son propre rate limiter** (les limites Riot sont par
  région) : fenêtres **glissantes** (quand une fenêtre est pleine on attend
  uniquement l'expiration de la requête la plus ancienne — jamais de vidange
  complète ni de sleep fixe par requête). Budgets par défaut, avec marge sur
  les limites 20/s et 100/2min d'une clé personnelle : 18 req/s et 84 req/2min
  (~42 req/min) sur asia et americas, **14 req/s et 68 req/2min (~34 req/min)
  sur europe** pour laisser du budget à lol-live-coach (voir ci-dessous).
  Un 429 bloque la région le temps du header `Retry-After` ; les 5xx sont
  retentés avec backoff exponentiel ; un 404 (match supprimé) ne déclenche
  aucun backoff. Overrides via `RATE_LIMIT_<REGION>` dans `.env` ;
  `LOG_LEVEL=DEBUG` trace chaque acquire (attente décidée + occupation des
  fenêtres). Tests : `python3 tests/test_ratelimit.py [--real]`.
- **Sampling** : League-Exp-V4 par buckets de tiers — `IRON_BRONZE`,
  `SILVER_GOLD`, `PLAT_EMERALD`, `DIAMOND_PLUS` — en round-robin pour un
  dataset équilibré. Pour chaque joueur : puuid → match ids Match-V5
  (queue 420, 20 max) **limités à une fenêtre glissante de
  `MATCH_MAX_AGE_DAYS` jours** (28 par défaut) via le paramètre `startTime`,
  filtré côté serveur Riot — les vieux matchs des joueurs inactifs ne
  coûtent aucune requête, et un joueur sans match dans la fenêtre est
  ignoré immédiatement. Détail des matchs **non encore en base** ensuite
  (la dédup se fait avant de dépenser la requête de détail).
- **Curseurs persistés** (`sampling_state`) par région × bucket : après un
  crash ou un restart, le parcours du ladder reprend où il s'était arrêté.
- **Patch** : `versions.json` de Data Dragon est relu au démarrage puis
  toutes les 6h ; un changement de version logge un événement
  `PATCH_CHANGE` bien visible. Le champ `patch` d'un match = les deux
  premiers segments de `gameVersion` (ex. `16.14`).
- Logs rotatifs dans `logs/collector.log` (5 × 10 Mo), pid dans
  `collector.pid`, lancement `nohup` via `start.sh` / arrêt via `stop.sh`.

## Installation & lancement

```bash
pip install -r requirements.txt        # aiohttp uniquement
cp .env.example .env                   # puis renseigner RIOT_API_KEY
./start.sh                             # nohup, logs dans logs/
./stop.sh                              # arrêt gracieux
python3 collector.py stats             # état du dataset
python3 collector.py export --study tierlist --out /tmp/export   # patch courant auto
```

## Schéma SQLite (`data/matches.db`)

- `matches(match_id PK, region, platform, game_version, patch, game_duration,
  game_creation, tier_bucket_source, inserted_at)`
- `participants(match_id, puuid, champion_id, champion_name, team_id,
  team_position, win, kills, deaths, assists, item0..item6,
  perk_primary_style, perk_sub_style, perk_keystone, gold_earned, total_cs,
  patch)` — `patch` dénormalisé pour l'index `(champion_id, patch)`
- `bans(match_id, team_id, champion_id, pick_turn)` — `champion_id = -1`
  signifie « pas de ban »
- `team_objectives(match_id, team_id, first_blood, first_tower, first_dragon,
  first_baron, dragon_kills, baron_kills, tower_kills, herald_kills,
  horde_kills)` — `herald_kills` vient de `objectives.riftHerald` (**Rift
  Herald uniquement**) et `horde_kills` de `objectives.horde` (**voidgrubs**) :
  voir la correction documentée plus bas
- `timeline_events(match_id, timestamp_ms, type, team_id, killer_id,
  victim_id, monster_type, monster_subtype, lane_type, building_type,
  position_x, position_y)` — types conservés : `CHAMPION_KILL`,
  `ELITE_MONSTER_KILL`, `BUILDING_KILL`, `TURRET_PLATE_DESTROYED`
- `timeline_frames(match_id, minute, participant_id, total_gold,
  current_gold, xp, level, cs, position_x, position_y)` — une ligne par
  joueur et par minute
- `timeline_state(match_id, status, fetched_at)` — `ok` / `skipped` (hors
  échantillon) / `missing` (404 Riot) : évite de re-dépenser une requête
- `sampling_state`, `meta` : état interne (curseurs, version ddragon)

Index : `matches(patch, tier_bucket_source)`, `participants(champion_id, patch)`,
plus trois **index couvrants d'export** créés automatiquement au premier
export (`matches(patch, region, tier_bucket_source, match_id)`,
`participants(match_id, champion_id, win)`, `bans(match_id, champion_id)`) —
les requêtes d'agrégation ne touchent jamais les tables, uniquement les
index (vérifié par `EXPLAIN QUERY PLAN` à chaque export).

## Timelines (études temporelles)

Le collecteur récupère aussi les **timelines** Match-V5 (`/timelines`) —
objectifs horodatés, or et XP à la minute, positions — pour permettre des
études comme « side au premier drake à or égal ».

- **Échantillonnage** : une timeline coûte une requête supplémentaire par
  match ; les collecter toutes diviserait par deux le débit de matchs. Seule
  une fraction est prise, réglée par `TIMELINE_SAMPLE_RATE` (**0.33** par
  défaut). Le tirage est **déterministe à partir du `match_id`**
  (SHA-256, pas de `random`) : deux exécutions retiennent exactement les mêmes
  matchs, la rétro-collecte porte sur le même sous-ensemble que la collecte en
  direct, et baisser le taux ne fait que retirer des matchs (jamais en
  échanger). Les tier lists gardent donc leur volume complet.
- **Une timeline ratée ne coûte pas le match** : l'erreur est loggée, le match
  reste en base, et le `timeline_state` permettra de réessayer.

### Volume — à lire avant d'augmenter le taux

Mesuré sur 300 timelines synthétiques de 18 à 40 minutes (index compris,
après `VACUUM`) : **27,2 Ko par match**, soit ~95 événements retenus et ~299
frames (10 joueurs × ~30 minutes).

| Taux | Un patch (786 509 matchs) | Base entière (2,19 M matchs) |
| --- | --- | --- |
| 0.10 | 2,0 Go | 6,0 Go |
| **0.33** (défaut) | **6,7 Go** | 18,8 Go |
| 1.00 | 20,4 Go | 56,9 Go |

La base fait déjà ~6 Go : à 0.33 sur le seul patch courant, elle **double**.
Les `timeline_frames` représentent l'essentiel du volume (299 lignes/match
contre 95 pour les events).

**C'est pourquoi le taux ne pilote pas seul la collecte** :
`TIMELINE_TARGET_PER_PATCH` (**200 000** par défaut) plafonne le nombre de
timelines stockées **par patch**. Une fois le plafond atteint, la collecte de
timelines s'arrête (un log l'indique une fois), les matchs continuent d'être
collectés normalement, et **tout repart automatiquement au patch suivant**.
Sans ce plafond, à ~157 k matchs/jour, un taux de 0.33 remplirait ~25 Go en
deux semaines. À 200 000 timelines : **~5,2 Go par patch**, borné. Mettre `0`
pour désactiver le plafond (déconseillé). Le plafond s'applique aussi au
backfill, qui le contournerait sinon.

### Rétro-collecte

```bash
python3 collector.py backfill-timelines --limit 5000 [--share 0.3]
```

Récupère les timelines de matchs **déjà en base**, patch courant en priorité
puis les patchs précédents du plus récent au plus ancien, en ne prenant que
les matchs retenus par l'échantillonnage déterministe.

Le backfill a **son propre rate limiter par région**, dimensionné à une
fraction (`--share`, 0.3 par défaut) du budget régional : lancé pendant que
le collecteur tourne, il se contente de ~30 % des requêtes de la région et
n'affame pas les workers. Les limites Riot étant par clé et par région, cette
part est à ajuster selon ce qu'on veut privilégier.

### Purge des vieux patchs

```bash
python3 collector.py prune --keep-patches 2 [--exports site/data/etudes] [--yes]
```

Supprime les **matchs bruts** des patchs au-delà des N derniers, avec leurs
participants, bans, objectifs et timelines, puis compacte la base (`VACUUM`)
en annonçant l'espace libéré.

**Garde-fou** : un patch n'est purgé que si un **export agrégé existe** pour
lui (`site/data/etudes/<famille>/<patch-slug>/meta.json`). Les patchs sans
export sont listés et laissés intacts — les supprimer serait une perte
définitive. C'est cohérent avec l'architecture du site : **les études
publiées ne dépendent que des JSON exportés, jamais de la base**, donc un
patch exporté puis purgé reste consultable en ligne.

Confirmation interactive par défaut ; `--yes` pour un usage en cron.

### Correction : voidgrubs ≠ Rift Herald

Audit du parsing existant : `herald_kills` lisait bien `objectives.riftHerald`
(donc le Rift Herald seul, **sans confusion**), mais `objectives.horde` — les
**voidgrubs** — n'était **ni stocké ni lu** : ces objectifs étaient purement
et simplement perdus. Une colonne `horde_kills` a été ajoutée à
`team_objectives` (migration automatique par `ALTER TABLE` sur une base
existante, sans la recréer). Côté timeline, `ELITE_MONSTER_KILL` conserve
`monster_type`, où `HORDE` (voidgrubs) et `RIFTHERALD` restent distincts.
Les matchs collectés **avant** cette correction ont `horde_kills` à `NULL` :
c'est attendu, et distinguable d'un vrai `0`.

## Publication d'une étude EloLab (à chaque patch)

1. **Exporter** depuis le serveur (patch courant détecté via Data Dragon,
   jamais codé en dur — `--patch X.Y` pour un patch passé) :

   ```bash
   python3 collector.py export --study tierlist --out /tmp/export-tierlist
   ```

   Produit `tierlist.json` (champion × bucket × région : games, wins,
   winrate + intervalle de Wilson à 95 %, pick/ban rates,
   `insufficient_sample` sous 200 games) et `meta.json` (patch, période de
   collecte, échantillon total et par cellule, régions). Le premier export
   crée les index (une seule fois, quelques minutes sur une grosse base) ;
   les suivants prennent quelques dizaines de secondes.

2. **Déposer les données** dans le repo (slug = patch avec des tirets) :

   ```bash
   mkdir -p site/data/etudes/tierlist/16-15
   cp /tmp/export-tierlist/*.json site/data/etudes/tierlist/16-15/
   ```

3. **Créer le contenu** `site/content/etudes/tierlist/16-15/` :
   `index.mdx` (partir de la version du patch précédent) + `meta.json`
   (title, date, patch, patch_sensitive, sample_size, regions, collected_at,
   tags — build en échec si un champ manque). La rédaction suit la
   [charte éditoriale](docs/editorial.md) ; le skill
   `.claude/skills/elolab-redaction/` l'applique automatiquement dans une
   session Claude Code.

4. **Vérifier et publier** :

   ```bash
   python3 scripts/verify_study.py site/content/etudes/tierlist/16-15
   cd site && npm run build     # doit passer, sinon le meta.json est incomplet
   git checkout -b etude/tierlist-16-15 && git add . && git commit && git push
   ```

   `verify_study.py` est le garde-fou de la promesse « des données, pas des
   impressions » : il extrait chaque nombre du MDX et le vérifie contre les
   JSON exportés, en tenant compte du champion dont parle le paragraphe.
   Il sort en **code 1** si un chiffre ne concorde pas.

   La PR mergée déclenche le déploiement Vercel ; `/etudes/tierlist`
   redirige automatiquement vers le nouveau patch, l'ancienne version reste
   accessible à son URL datée, et le sélecteur de patch liste l'archive.

## Limites connues (assumées)

- **`tier_bucket_source` est une approximation** : c'est le bucket du joueur
  échantillonné qui a mené au match, pas le rank moyen réel de la game
  (Match-V5 n'expose pas le rank des participants). Une game taguée
  `SILVER_GOLD` peut contenir des joueurs d'autres tiers proches.
- **Biais d'échantillonnage** : les joueurs actifs (beaucoup de games
  récentes) sont surreprésentés ; le parcours du ladder par pages
  League-Exp n'est pas un tirage uniforme ; une plateforme par région
  (euw1/kr/na1) sert de proxy pour tout le routing.
- **Fenêtre de collecte (biais voulu)** : seuls les matchs de moins de
  `MATCH_MAX_AGE_DAYS` jours (28 par défaut) sont collectés — les joueurs
  inactifs sont donc exclus de la collecte future. C'est assumé : le but
  est d'étudier la méta courante, pas l'historique des inactifs. Les
  matchs déjà en base, eux, sont conservés quel que soit leur âge (utiles
  pour les études méta long terme).
- Un même match peut être atteint via plusieurs joueurs : il n'est stocké
  qu'une fois, avec le bucket du **premier** joueur qui l'a fait découvrir.
- La **Personal API Key expire toutes les 24h** : le collecteur s'arrête
  avec un log explicite sur 401/403 ; régénérer la clé, mettre à jour
  `.env`, relancer `./start.sh`.

## Jalons Todoist (`milestone_check.py`)

Script cron autonome (stdlib uniquement) qui surveille le volume collecté :

- à chaque seuil de `MILESTONES` franchi **sur le patch courant** (vu par le
  collecteur via ddragon), il crée une tâche Todoist p2 « EloLab : N matchs
  collectés en X.Y » dans le projet `TODOIST_PROJECT_NAME` (« LoL Studies »,
  résolu par nom via l'API), avec la répartition région × bucket ;
- si aucun match n'a été inséré depuis 3 h, tâche p1 « EloLab : le collecteur
  semble arrêté » (une seule fois tant que la panne dure, réarmée à la reprise) ;
- un seuil n'est notifié qu'une fois par patch (état dans
  `data/milestones_done.json`) ; nouveau patch → seuils de nouveau notifiables ;
- idempotent et silencieux quand rien à signaler ; logs dans
  `logs/milestones.log`. Nécessite `TODOIST_API_TOKEN` dans `.env`.
  Utilise l'**API Todoist unifiée v1** (`api.todoist.com/api/v1` — l'ancienne
  REST v2 est retirée et renvoie 410). Plusieurs seuils en attente (ex :
  rattrapage après une panne de notification) donnent **un seul message
  récapitulatif**. Valider la config en une commande :

```bash
python3 milestone_check.py --dry-run   # teste token + accès projet, ne crée rien
```

Ligne cron à installer (`crontab -e`, toutes les 30 min) :

```cron
*/30 * * * * cd /home/aristide/lol-studies-collector && /usr/bin/python3 milestone_check.py
```

## Cohabitation avec lol-live-coach (même clé API)

Les rate limits Riot sont **par clé et par région**. Si lol-live-coach tourne
sur le même serveur avec la même `RIOT_API_KEY` (euw1 → routing europe), le
collecteur et le coach partagent le budget europe. C'est pour ça que le
collecteur se limite par défaut à 14 req/s / 72 req/2min sur europe : le
coach garde ~25 req/2min pour ses briefs live et analyses post-game sans se
prendre de 429. Si un pic arrive quand même, le collecteur encaisse le 429
(Retry-After) et cède la place. **Quand tu régénères la clé (expiration
24h), mets à jour les deux `.env`** : celui du collecteur et celui du coach.

## Mention légale Riot

À afficher sur tout site/étude publiant ces données :

> *lol-studies-collector isn't endorsed by Riot Games and doesn't reflect the
> views or opinions of Riot Games or anyone officially involved in producing
> or managing Riot Games properties. Riot Games, and all associated
> properties are trademarks or registered trademarks of Riot Games, Inc.*
