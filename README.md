# lol-studies-collector

Collecteur continu de matchs **ranked solo/duo (queue 420)** via l'API Riot,
multi-régions, vers une base SQLite, pour des études pick/ban/winrate par
rank et région.

## Architecture

- **3 workers asyncio indépendants**, un par routing régional :
  `europe` (échantillonne euw1), `asia` (kr), `americas` (na1).
- Chaque worker a **son propre rate limiter** (les limites Riot sont par
  région) : fenêtres glissantes 18 req/s et 90 req/2min (marge de 10 % sur
  les limites 20/s et 100/2min d'une clé personnelle). Un 429 bloque la
  région le temps du header `Retry-After` ; les 5xx sont retentés avec
  backoff exponentiel.
- **Sampling** : League-Exp-V4 par buckets de tiers — `IRON_BRONZE`,
  `SILVER_GOLD`, `PLAT_EMERALD`, `DIAMOND_PLUS` — en round-robin pour un
  dataset équilibré. Pour chaque joueur : puuid → 20 derniers match ids
  Match-V5 (queue 420) → détail des matchs **non encore en base** (la dédup
  se fait avant de dépenser la requête de détail).
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
python3 collector.py export --study tierlist --patch 16.14 --out tierlist.csv
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
  first_baron, dragon_kills, baron_kills, tower_kills, herald_kills)`
- `sampling_state`, `meta` : état interne (curseurs, version ddragon)

Index : `matches(patch, tier_bucket_source)`, `participants(champion_id, patch)`.

## Limites connues (assumées)

- **`tier_bucket_source` est une approximation** : c'est le bucket du joueur
  échantillonné qui a mené au match, pas le rank moyen réel de la game
  (Match-V5 n'expose pas le rank des participants). Une game taguée
  `SILVER_GOLD` peut contenir des joueurs d'autres tiers proches.
- **Biais d'échantillonnage** : les joueurs actifs (beaucoup de games
  récentes) sont surreprésentés ; le parcours du ladder par pages
  League-Exp n'est pas un tirage uniforme ; une plateforme par région
  (euw1/kr/na1) sert de proxy pour tout le routing.
- Un même match peut être atteint via plusieurs joueurs : il n'est stocké
  qu'une fois, avec le bucket du **premier** joueur qui l'a fait découvrir.
- La **Personal API Key expire toutes les 24h** : le collecteur s'arrête
  avec un log explicite sur 401/403 ; régénérer la clé, mettre à jour
  `.env`, relancer `./start.sh`.

## Mention légale Riot

À afficher sur tout site/étude publiant ces données :

> *lol-studies-collector isn't endorsed by Riot Games and doesn't reflect the
> views or opinions of Riot Games or anyone officially involved in producing
> or managing Riot Games properties. Riot Games, and all associated
> properties are trademarks or registered trademarks of Riot Games, Inc.*
