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
python3 collector.py refresh --study tierlist   # arrêt, export, redémarrage
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
`participants(match_id, champion_id, team_position, win)`,
`bans(match_id, champion_id)`) —
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

1. **Rafraîchir les données** depuis le serveur. Une seule commande :

   ```bash
   python3 collector.py refresh --study tierlist
   ```

   Elle arrête le collecteur, exporte le patch courant vers sa destination
   déduite (`site/data/etudes/tierlist/<patch-slug>/`), redémarre le
   collecteur et affiche un résumé (patch, matchs, cellules valides).

   **Le collecteur est redémarré même si l'export échoue.** Une étude non
   rafraîchie est un contretemps ; un collecteur laissé à l'arrêt, c'est de
   la donnée définitivement perdue — les matchs sortent de la fenêtre de
   rétention de Riot.

   L'export produit trois fichiers :

   - `tierlist.json` : champion × bucket × région, tous rôles confondus —
     games, wins, winrate + intervalle de Wilson à 95 %, pick/ban rates,
     `insufficient_sample` sous 200 games ;
   - `tierlist-roles.json` : les mêmes cellules découpées par poste
     (`team_position` : TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY), réduites aux
     compteurs bruts (voir « Dimension rôle » plus bas) ;
   - `meta.json` : patch, période de collecte, échantillon total et par
     cellule, régions, nombre de cellules exploitables.

   Le premier export crée les index (une seule fois, quelques minutes sur
   une grosse base) ; les suivants prennent quelques dizaines de secondes.

   Pour un patch passé ou une destination ad hoc, `export` reste
   disponible : `python3 collector.py export --study tierlist --patch 16.15
   [--out <dir>]`. Sans `--out`, la destination est déduite du patch.

2. **Créer le contenu** `site/content/etudes/tierlist/16-15/` :
   `index.mdx` (partir de la version du patch précédent) + `meta.json`
   (title, date, patch, patch_sensitive, sample_size, regions, collected_at,
   tags — build en échec si un champ manque). La rédaction suit la
   [charte éditoriale](docs/editorial.md) ; le skill
   `.claude/skills/elolab-redaction/` l'applique automatiquement dans une
   session Claude Code.

3. **Vérifier et publier** :

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

4. **Annoncer**, une fois le déploiement terminé (l'annonce pointe vers la
   page en ligne, inutile de l'envoyer avant) :

   ```bash
   git pull                                # récupérer le contenu mergé
   python3 scripts/notify_discord.py --dry-run   # relire l'embed
   python3 scripts/notify_discord.py
   ```

   Le flux RSS, lui, ne demande rien : il est régénéré par le build Vercel.

## Dimension rôle

Les cellules d'export existent en deux découpages :

- `tierlist.json` — champion × bucket × région, **tous rôles confondus** ;
- `tierlist-roles.json` — champion × bucket × région × **poste**.

Le poste vient exclusivement de `participants.team_position`, renvoyé par
Riot, **jamais d'une liste de champions présumée** : un Yasuo support joué
1 200 fois compte en `UTILITY`, comme il doit l'être.

Les deux fichiers sortent de la **même passe SQL** — les agrégats tous rôles
sont la somme des cellules par poste, ils ne peuvent pas diverger. Les
participations dont `team_position` est vide (remakes, données Riot
incomplètes) sont comptées dans les agrégats tous rôles mais ne produisent
aucune cellule de poste : inventer un rôle serait pire que ne pas en donner.

Le seuil des 200 games s'applique **par cellule**, donc à la cellule de
poste, qui est cinq fois plus petite. Beaucoup plus de cellules sont sous le
seuil dans ce découpage — c'est attendu, et c'est le but du seuil.

`tierlist-roles.json` est réduit aux compteurs bruts (`champion_id`,
`region`, `bucket`, `role`, `games`, `wins`). Trois absences volontaires :

- **pas de champs dérivés** (winrate, bornes de Wilson, pick rate) : ni le
  tableau du site ni `verify_study.py` ne lisent ceux de `tierlist.json`,
  les deux les recalculent avec la même formule. Ce fichier a cinq fois plus
  de lignes et il est servi dans la page — mesuré sur le patch 16.15 :
  10 380 cellules, **1 Mo brut mais 145 Ko une fois compressé**, contre
  ~4 Mo si on stockait les valeurs dérivées ;
- **pas de `champion_name`** : jointure par `champion_id` sur le fichier
  principal ;
- **pas de bans** : un ban vise un champion pour toute la partie, il n'a pas
  de poste. Écrire 0 laisserait croire que personne ne bannit ce champion à
  ce poste. Le tableau du site affiche « — » sur une sélection de poste.

## Garde-fous de l'export

Un export de 16.16 comptant 517 matchs et aucune cellule exploitable a
écrasé les JSON publiés de 16.15. Trois protections, testées par
`tests/test_export_guards.py` qui rejoue l'incident :

1. **La destination est déduite du patch** — `--out` devient optionnel.
   Rien à taper, donc rien à se tromper.
2. **Refus si le patch ne correspond pas à la destination**, détecté de deux
   façons indépendantes : le dossier porte un slug de patch différent, ou il
   contient déjà un `meta.json` d'un autre patch (le cas exact de
   l'incident). Sortie en code 1, aucun fichier touché. **`--force` ne
   contourne pas ce contrôle** : un patch qui n'est pas le bon est toujours
   une erreur.
3. **Refus si aucune cellule n'atteint `--min-games`** (200) — un export
   sans donnée exploitable n'est pas un export. Contournable par `--force`,
   qui le signale alors dans la sortie.
4. **Refus si l'export est nettement plus maigre que celui déjà publié pour
   le même patch** (moins de la moitié des matchs). Les contrôles ci-dessus
   ne voient pas le cas « bon patch, mauvaise base » : exporter depuis une
   base de test ou une copie tronquée les passe tous. C'est arrivé pendant
   l'écriture de ces garde-fous — 36 000 matchs synthétiques écrits
   par-dessus les 786 509 publiés. À patch constant le volume ne fait que
   croître, une chute nette n'est jamais légitime. Contournable par
   `--force`.

Les JSON sont écrits de façon **atomique** (fichier temporaire puis
`os.replace`) : un export interrompu ne laisse pas de fichier tronqué à la
place d'une étude publiée. Et les index d'export sont **reconstruits si leur
définition a changé** — sinon un index créé par une version antérieure
resterait en place et les requêtes retourneraient à la table pour 22 M de
lignes.

## Diffusion

### Flux RSS — `/rss.xml`

Généré au build depuis `site/content/etudes/**/meta.json` : titre,
description, date, lien et patch (`<category>`) de chaque étude, les plus
récentes en premier. Déclaré dans le `<head>` de toutes les pages
(`<link rel="alternate" type="application/rss+xml">`) pour l'auto-découverte
par les lecteurs, et lié discrètement depuis le footer.

Deux détails qui évitent des faux positifs de mise à jour côté lecteurs :
la `lastBuildDate` est celle de la dernière étude et non l'heure du build
(sinon chaque redéploiement ferait passer le flux pour modifié), et les
`pubDate` sont posées à midi UTC (à minuit, un lecteur à l'ouest de
Greenwich afficherait la veille).

### Annonce Discord — `scripts/notify_discord.py`

Poste un embed par étude : titre, chapô, chiffre-clé (valeur, IC et taille
d'échantillon), image OG et lien. Stdlib uniquement.

```bash
python3 scripts/notify_discord.py                  # annonce ce qui ne l'a pas été
python3 scripts/notify_discord.py --dry-run        # affiche le payload, n'envoie rien
python3 scripts/notify_discord.py --study tierlist/16-15
python3 scripts/notify_discord.py --init           # marque tout comme annoncé
```

- **Configuration** : `DISCORD_WEBHOOK_URL` dans `.env`. Absente, le script
  ne fait rien et **sort en 0** — une diffusion non configurée ne doit pas
  casser un pipeline de publication. L'URL n'est jamais écrite en dur ni
  journalisée (elle vaut mot de passe) ; un test le vérifie.
- **Idempotence** : état dans `data/notify_discord.json`, écrit après
  *chaque* envoi réussi et non en fin de lot — une coupure au milieu d'un
  rattrapage ne fait pas repartir ce qui est déjà passé. `--force` pour
  ré-annoncer volontairement.
- **Garde-fou machine neuve** : `data/` est gitignoré, donc sur un clone
  frais l'état est absent et tout l'historique passerait pour nouveau. Dans
  ce cas précis (état absent **et** plus d'une étude en attente) le script
  refuse d'envoyer et propose `--init` (tout marquer comme annoncé, sans
  envoi) ou `--force`.
- Un 429 est retenté en respectant `retry_after`, un 5xx avec un backoff.

Tests : `python3 tests/test_notify_discord.py` (un vrai serveur HTTP local
tient lieu de webhook — on vérifie ce qui part sur le réseau, pas ce que le
script prétend faire).

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
