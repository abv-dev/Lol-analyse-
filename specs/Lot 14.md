# Spec — Lot 14 : participant_id et achats d'objets légendaires

Statut : à faire · Bloque le Niveau 2 du Lot 1 · PR unique
Rédigée le 2026-08-14. Même logique que le Lot 13 : chaque jour de collecte
sans ces données est définitivement perdu (le JSON brut n'est pas conservé).
Chiffrage à l'appui (§5) : ~28 Mo/jour, mesuré, pas estimé.

## 1. Objectif

Trois extensions du collecteur, indépendantes du reste du plan :

1. **`participants.participant_id`** — l'identifiant Riot 1–10 du participant,
   sans lequel aucune métrique Timeline ne peut relier un joueur aux
   `victim_id`/`participant_id` des tables `timeline_events`/`timeline_frames`.
2. **`item_events`** — les achats (et annulations) d'**objets légendaires
   complétés**, extraits des timelines déjà collectées à 10 %. Débloque, pour
   les patchs futurs, la métrique « timestamp du 1er item légendaire complété »
   (`debloque_par` au §5.4 du brief).
3. **`timeline_events.assisting_ids`** — les assistants des `CHAMPION_KILL`
   (ajout du 14 août 2026). Porte la métrique Niveau 2 « part des morts avec
   ≥ 3 assistants ennemis » (v1, brief §5.4) : le signal de placement le plus
   direct de la liste, qu'aucune stat de fin de partie ne remplace.

## 2. `participants.participant_id`

### 2.1 Colonne et parsing
- Migration additive via le mécanisme `MIGRATIONS` existant (précédent :
  Lot 13) : `ALTER TABLE participants ADD COLUMN participant_id INTEGER`.
- `store_match` renseigne `info.participants[].participantId` pour toute
  nouvelle insertion. NULL interdit sur les nouvelles lignes (le champ est
  toujours présent dans Match-V5).

### 2.2 Backfill historique — dérivation validée
Fait vérifié (2026-08-14, base réelle) : le rang d'insertion des lignes d'un
match (`ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY rowid)`) est égal au
`participantId` Riot — confirmé contre `info.participants` de trois timelines
réelles (mapping puuid ↔ participantId identique 30/30). Cause structurelle :
`store_match` insère via un unique `executemany` dans l'ordre du payload.

- Le backfill des lignes historiques utilise cette dérivation, en un seul
  `UPDATE` à froid.
- **Application encadrée** : `stop.sh` → migration + backfill → vérification →
  `start.sh`, comme au Lot 13. C'est la seule écriture autorisée sur la base
  de production, et elle se fait collecteur arrêté.
- Garde-fou intégré à la migration : après backfill, contrôle que chaque
  `match_id` porte exactement les participant_id 1–10 sans doublon (matchs à
  10 participants) ; échec = rollback, code de sortie non nul.

## 3. `item_events` — achats légendaires

### 3.1 Filtre « objet légendaire complété »
Liste d'`item_id` construite depuis Data Dragon (`item.json` de la version
courante, déjà interrogée par le collecteur pour `ddragon_current`) :
achetable (`gold.purchasable`), sans amélioration ultérieure (`into` vide),
`gold.total ≥ 2000`, disponible sur la carte 11. Mesuré sur 16.16 :
138 objets.

**Traçabilité** : la liste utilisée est stockée en base —
table `legendary_items (patch TEXT, item_id INTEGER, PRIMARY KEY (patch,
item_id))` + la version Data Dragon dans `meta`. Toute étude aval peut
reconstituer exactement le filtre appliqué.

Chargement : au démarrage et au premier match d'un patch inconnu ; en cas
d'échec réseau, retomber sur la liste du patch précédent (journalisé), retenter
périodiquement. Ne jamais bloquer la collecte de timelines pour ça.

### 3.2 Table et parsing
```sql
CREATE TABLE IF NOT EXISTS item_events (
    match_id       TEXT NOT NULL,
    participant_id INTEGER,
    item_id        INTEGER,   -- itemId (achat) ou beforeId (annulation)
    timestamp_ms   INTEGER,
    event          TEXT       -- 'PURCHASED' | 'UNDO'
);
CREATE INDEX IF NOT EXISTS idx_item_events_match ON item_events (match_id);
```
Dans `parse_timeline` : conserver `ITEM_PURCHASED` dont `itemId` est dans la
liste du patch, et `ITEM_UNDO` dont `beforeId` y est (une annulation d'achat
légendaire doit être visible, sinon le « 1er item » est faux). Ne PAS élargir
`KEPT_EVENT_TYPES` ni la table `timeline_events` : le chiffrage du §5 interdit
la variante brute.

`store_timeline` insère dans la même transaction que les events/frames
(DELETE préalable par match_id, comme les autres tables timeline). Le chemin
est partagé par le worker et `backfill-timelines` : aucun code spécifique au
backfill.

## 4. `timeline_events.assisting_ids` — assistants des kills

- Migration additive via `MIGRATIONS` :
  `ALTER TABLE timeline_events ADD COLUMN assisting_ids TEXT`. Les 5,5 M de
  lignes existantes restent NULL sans réécriture (colonne ajoutée en fin de
  schéma, hors de tout index).
- `parse_timeline`, sur les seuls événements `CHAMPION_KILL` : sérialiser
  `assistingParticipantIds` en texte `"6,8,10"` (ids dans l'ordre du payload).
  **Kill sans assistant → chaîne vide `''`, jamais NULL.** NULL = non collecté
  (ligne antérieure au Lot 14), `''` = mesuré sans assistant — même principe
  que les colonnes du Lot 13, à commenter dans le DDL. Les autres types
  d'événements restent NULL.
- Aucun backfill possible : le JSON brut des timelines n'est pas conservé.
  Les lots aval excluent les lignes NULL de cette métrique **seulement**, avec
  effectifs par métrique (mécanisme déjà spécifié au Lot 1).

## 5. Tests — `tests/test_item_events.py`

Sur base fixture uniquement. Cas minimum :
1. Migration idempotente (deux applications = même schéma), base à l'ancien
   schéma acceptée.
2. Backfill `participant_id` : fixture avec ordre d'insertion connu → rang =
   participantId attendu ; garde-fou déclenché sur une fixture à doublon.
3. `store_match` renseigne `participant_id` depuis un payload Match-V5 réel.
4. `parse_timeline` sur une timeline réelle (fixture à constituer, comme la
   fixture Match-V5 du Lot 13) : seuls les achats/annulations légendaires
   ressortent, avec les bons participant_id/timestamps ; un `ITEM_PURCHASED`
   non légendaire n'apparaît pas.
5. Liste légendaire : filtre appliqué depuis `legendary_items`, repli sur le
   patch précédent en cas de liste absente (journalisé).
6. `assisting_ids` : timeline réelle → `"6,8"` sur un kill assisté, `''` sur
   un solo kill, NULL sur les types non-`CHAMPION_KILL` ; migration
   idempotente ; les lignes pré-migration restent NULL.
7. Suite existante intacte et verte (contrainte n°7 du brief).

## 6. Chiffrage — pourquoi cette variante et pas une autre

Mesures du 2026-08-13/14 (4 timelines réelles 16.16 ; volumétrie `dbstat` sur
la base réelle ; rythme : ~14 150 timelines/jour) :

| Variante | Lignes/match | o/ligne | Coût/jour | Verdict |
|---|---|---|---|---|
| Tout `ITEM_PURCHASED` dans `timeline_events` | ~172 (moy., remakes inclus) | 150 (table + 2 index, mesuré) | **~365 Mo** | rejetée (≈ 11 Go/mois, 25 Go libres) |
| Légendaires seuls, table dédiée à index unique | ~31 (30 achats + ~1 undo) | ~65 | **~28 Mo** | **actée** |
| `assisting_ids` sur `CHAMPION_KILL` | ~61,5 kills/match (mesuré en base) | ~4 o **en plus** par kill (1,33 assistant/kill mesuré sur 195 kills, hors index) | **~3,5 Mo** | **actée** |

La variante actée capture exactement le besoin de la métrique (« 1er item
légendaire complété ») ; l'historique d'achats complet n'a pas de consommateur
dans le brief et coûterait 13× plus. `assisting_ids` est quasi gratuit : la
colonne s'ajoute à des lignes déjà conservées.

## 7. Critère de succès du lot

- `pytest tests/test_item_events.py` vert + suite existante verte ;
- migration appliquée en production, encadrée par `stop.sh`/`start.sh`,
  garde-fou du §2.2 passé ;
- après redémarrage : les nouvelles lignes `participants` portent
  `participant_id`, `item_events` se peuple sur les nouvelles timelines, et
  les nouveaux `CHAMPION_KILL` portent `assisting_ids` non NULL (contrôle en
  lecture seule).
