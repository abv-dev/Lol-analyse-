# Spec — Lot 3 : file éditoriale (`queue/`, `lolcollector/editorial.py`)

Statut : **partiellement livré** sur la branche `editorial/file-articles`
(commit `1e550f3`) · Dépend de : — · Spec **alignée sur le code livré** le
2026-08-18.

La première version de cette spec a été écrite sans connaissance de cette
branche. Le code est testé et tranche mieux que le document sur quatre points
(§3, §4, §5) : c'est la spec qui s'aligne. Ce qui suit décrit donc l'existant,
puis, au §8 seulement, ce qui reste à ajouter.

## 1. Ce qui existe déjà

| fichier | rôle |
|---|---|
| `lolcollector/editorial.py` | état des données, règle de sélection, instanciation |
| `queue/templates.json` | 11 gabarits récurrents |
| `queue/articles.json` | la file (aujourd'hui vide, voir §7) |
| `scripts/queue_sync.py` | instancie les gabarits pour un patch |
| `scripts/queue_status.py` | état de la file, sortie `--json` pour le Lot 4 |
| `tests/test_editorial_queue.py` | tests hors-ligne, base SQLite fixture |

Écriture atomique (`tmp` + `os.replace`) déjà en place dans `save_queue`.

## 2. Modèle de données

Article : `id`, `slug`, `famille`, `titre`, `angle`, `regime`,
`donnees_requises`, `patch_cible`, `debloque_par`, `statut`, `tentatives`,
`gabarit`, `jour_prefere`.

Gabarit : `id`, `titre_template`, `slug_template`, `famille`, `angle`,
`regime`, `donnees_requises`, `jour_prefere`, `variantes`.

Substitutions : `{patch}`, `{patch-slug}`, plus les clés de la variante
(`{role}`, `{role_label}`, `{role_slug}`). L'identifiant instancié est
`<gabarit>[-<role_slug>]-<patch-slug>`.

Statuts : `en_attente` → `donnees_pretes` → `redige` → `verifie` → `publie`,
plus `bloque` à 3 tentatives (`MAX_ATTEMPTS`). Le stock prêt à publier, c'est
`verifie` seul (`IN_STOCK`).

## 3. Disponibilité des données — la correction principale

`donnees_requises` ne nomme **pas des fichiers exportés** mais des **données de
la base**, chacune avec une requête et un volume minimal (`DATA_CHECKS`) :
`matches` (1 000), `participants` (10 000), `bans` (10 000), `team_position`
(50 000), `team_id` (10 000), `tier_bucket_source` (1 000), `region` (2 régions
distinctes), `timeline_events` (100 000), `timeline_frames` (500 000),
`horde_kills` (10 000), `team_objectives` (10 000), `patch_precedent` (50 000).

C'est plus juste que la version documentaire précédente : un fichier présent ne
dit rien de son contenu, un compte de lignes au-dessus d'un seuil, si.

Deux principes déjà codés, à conserver tels quels :

- **Une donnée inconnue n'est jamais disponible.** Une faute de frappe dans
  `donnees_requises` bloque l'article au lieu de le laisser passer. La
  défaillance est fermée.
- **La portée dépend du régime.** `patch_courant` et `comparatif` comptent
  **sur le patch courant** ; `structurel` compte **sur toute la base**. Scoper
  une structurelle au patch courant éteindrait le réservoir le jour d'un
  patch — précisément le jour où il doit prendre le relais. Ce point n'était
  pas dans la spec initiale et il est décisif.

## 4. `debloque_par`

Même vocabulaire que `donnees_requises` : un nom de donnée (ou une liste),
évalué avec le même seuil de volume et la même portée. Tant que le compte n'y
est pas, l'article est **invisible à la rédaction** ; il le redevient de
lui-même quand la collecte rattrape, sans qu'aucune écriture n'ait lieu dans la
file.

Un seul espace de noms pour les deux champs, au lieu des quatre familles de
prédicats (`fichier:`, `export:`, `colonne:`, `patch_complet:`) que proposait
la version précédente : moins de surface, un seul endroit à corriger, et le
seuil de volume dit déjà ce que `patch_complet:` cherchait à dire.

## 5. Règle de sélection

`blockers(article, state)` rend la liste des raisons — vide = rédigeable.
Ordre des tests : statut, puis régime, puis données.

**Maturité du patch**, pour `patch_courant` uniquement : au moins
`QUEUE_MIN_PATCH_AGE_DAYS` jours (3) **et** `QUEUE_MIN_PATCH_MATCHES` matchs
(50 000), les deux réglables par environnement. Le brief ne donnait que les
trois jours ; le plancher de volume est un ajout du code, et il a raison —
trois jours avec un collecteur à l'arrêt ne font pas un échantillon.
`comparatif` est rédigeable dès le premier jour (c'est son sujet), `structurel`
tous les jours de l'année.

**Ordre de priorité, inversé quand le stock est bas**
(`selection_order`, seuil `--stock-min`, 15 par défaut) :

| stock | ordre |
|---|---|
| ≥ seuil | `patch_courant` → `comparatif` → `structurel` |
| < seuil | `structurel` → `comparatif` → `patch_courant` |

Remplir le tampon d'abord quand il se vide, parce que le structurel ne périme
pas et qu'il est le seul disponible le jour d'un patch. La spec initiale
proposait un ordre figé (le plus périssable d'abord) : c'est le bon réflexe
quand le stock est plein, et le mauvais quand il est bas. Le code a raison.

Départage : `jour_prefere` (0 = lundi … 6 = dimanche), **indication
d'étalement, pas contrainte**, puis `id`.

## 6. Les 11 gabarits

8 `patch_courant`, dont `meta-role` qui produit 5 articles par ses variantes,
et 3 `comparatif` : **15 articles instanciés par patch** (chiffre couvert par
les tests). Pour un cycle de patch d'environ 14 jours, la cadence quotidienne
est atteinte par les seuls gabarits récurrents — le réservoir structurel est un
amortisseur, pas la source.

| id | famille | régime | données requises |
|---|---|---|---|
| `tierlist-globale` | `tierlist` | patch_courant | matches, participants |
| `tierlist-par-rank` | `tierlist-rank` | patch_courant | tier_bucket_source |
| `meta-role` ×5 | `meta-role` | patch_courant | team_position |
| `bans-justifies` | `bans` | patch_courant | bans |
| `champions-pieges` | `champions-pieges` | patch_courant | matches, participants |
| `deep-dive-plus-banni` | `deep-dive` | patch_courant | bans, team_position |
| `duos-botlane` | `duos` | patch_courant | team_position, team_id |
| `comparaison-regionale` | `regions` | patch_courant | region |
| `patch-change` | `patch-compare` | comparatif | patch_precedent |
| `premieres-tendances` | `tendances` | comparatif | matches |
| `vitesse-adoption` | `adoption` | comparatif | patch_precedent |

## 7. Le réservoir structurel

`queue/articles.json` est vide, avec la note « les 17 sujets structurels n'ont
pas été transmis ». Voici l'inventaire, relevé sur le schéma réel, à ajouter
avec `regime: "structurel"`, `patch_cible: null` :

| sujet | `donnees_requises` | `debloque_par` |
|---|---|---|
| écart Fer-Bronze / Diamant+ | tier_bucket_source, participants | — |
| picks régionaux | region, participants | — |
| bans par bucket | bans, tier_bucket_source | — |
| blue side / red side | team_id, participants | — |
| durée de game et winrate | matches | — |
| premier drake | team_objectives | — |
| premier sang, première tourelle | team_objectives | — |
| larves du Néant vs drake | team_objectives | horde_kills |
| dégâts par poste | team_position | damage_to_champions ¹ |
| vision score par bucket | tier_bucket_source | vision_score ¹ |
| CS à 10 min par bucket | tier_bucket_source | cs_at10 ¹ |
| temps passé mort | participants | time_spent_dead ¹ |
| pinks achetés | participants | control_wards_bought ¹ |
| plates de tourelle | team_position | turret_plates_taken ¹ |
| abandons précoces | matches | early_surrender ¹ |
| écart de gold à 15 min | timeline_frames | — |
| morts avant 5 minutes | timeline_events | — |
| premier objet légendaire | timeline_events | item_events ¹ |
| morts avec ≥ 3 assistants | timeline_events | assisting_ids ¹ |

¹ Entrée à **ajouter à `DATA_CHECKS`** : ce sont les colonnes des lots 13 et
14, NULL sur tout l'historique antérieur à leur migration. La requête compte
les lignes non NULL sur la portée voulue, avec un seuil de volume — c'est
exactement le mécanisme existant, il suffit d'ajouter les lignes. Aucune de ces
requêtes ne doit scanner `participants` (22 M de lignes, contrainte n°4 du
brief) : les compter sur le patch courant passe par
`idx_participants_champ_patch`, à vérifier par `EXPLAIN QUERY PLAN` avant de
figer les seuils.

## 8. Ce qui reste à ajouter au code

Par ordre d'importance.

### 8.1 `verifieur` — le vrai plafond de la cadence (Lot 15)

Le code décide si la **donnée brute** est là ; il ne dit rien de la capacité de
`scripts/verify_study.py` à juger l'article qui en sortira. Or la couche A ne
sait lire qu'un profil de données : `tierlist.json` en dur dans `load_study`,
six clés attendues par `StudyValues`.

Conséquence, gabarit par gabarit :

| jugeable par la couche A aujourd'hui | non jugeable |
|---|---|
| `tierlist-globale`, `tierlist-par-rank`, `bans-justifies`, `champions-pieges`, `comparaison-regionale` | `meta-role` ×5 et `deep-dive-plus-banni` (données par poste), `duos-botlane` (aucun export), `patch-change`, `premieres-tendances`, `vitesse-adoption` (deux patchs en regard) |

**5 articles jugeables sur les 15 instanciés par patch.** Un article que la
couche A ne sait pas lire ne peut pas devenir `verifie`, donc ne peut pas être
publié : la cadence réelle est d'un article tous les trois jours, quel que soit
l'état de la collecte. C'est le Lot 15 (`specs/Lot 15.md`), en dépendance dure
du Lot 4.

Ajout : un champ `verifieur` sur le gabarit et l'article, et une entrée
`DATA_CHECKS` d'un genre nouveau — `verifieur:<profil>`, vraie quand
`verify_study.py --profils --json` déclare le profil. Même mécanique, même
défaillance fermée.

### 8.2 L'export n'est pas vérifié

`donnees_requises` atteste que la base contient la donnée ; rien n'atteste que
l'**export** existe sous `site/data/etudes/<famille>/<slug>/`. Un article peut
donc être « rédigeable » alors que le JSON que lira le précis (Lot 4 §5) et que
relira la couche A n'a pas été produit. Ajout : soit une vérification de
fichier, soit — mieux — le Lot 4 déclenche l'export manquant avant de rédiger.

### 8.3 Statut `perime`

Le code traite un patch dépassé comme un *blocage de rédaction*
(« vise le patch X, courant : Y »). Il manque le statut terminal côté
publication : le Lot 5 refuse un article dont le patch n'est plus courant et
doit pouvoir l'écarter définitivement. Ajout : `perime` dans `STATUSES`.

### 8.4 Verrou de rédaction

Aucun statut ne marque « rédaction en cours ». Un cron tué laisse un article en
`en_attente` alors qu'un rédacteur travaillait dessus. Ajout : `en_cours`
horodaté, expirant au bout d'une heure, sans consommer de tentative.

### 8.5 `queue/redige/<id>/`

L'article rédigé mais non publié n'a pas d'emplacement défini. Il ne doit pas
vivre dans `site/content/` — il apparaîtrait dans le build. Ajout : les
fichiers rédigés vont dans `queue/redige/<id>/` (`index.mdx` + `meta.json`),
et le Lot 5 les déplace en un geste, avec le commit.

### 8.6 Unicité par chemin

`sync_templates` déduplique par `id`. Deux articles de la file peuvent encore
viser le même `slug` — donc le même répertoire sous `site/content/etudes/` —
si l'un vient d'un gabarit et l'autre a été ajouté à la main. Ajout : refuser
un `slug` déjà présent dans la file **ou déjà présent sur le disque**.

### 8.7 `precis` sur les gabarits

Champ nommant la fonction qui condense les JSON pour le rédacteur (Lot 4 §5) :
le rédacteur ne voit jamais les données brutes.

## 9. Tests

`tests/test_editorial_queue.py` couvre déjà : 11 gabarits, 15 articles
instanciés, idempotence de `sync_templates` (aucun statut écrasé, aucun
doublon), détection du patch courant et du précédent, maturité (un patch d'un
jour ne porte aucun `patch_courant` mais laisse passer le structurel),
inversion de priorité sous le seuil de stock, `debloque_par` sur timelines et
voidgrubs, donnée inconnue, statuts terminaux, `stock_count`.

À ajouter avec le §8 : `verifieur` non déclaré ⇒ invisible ; export absent ⇒
non rédigeable ; `perime` ; expiration de `en_cours` ; collision de `slug` ;
les nouvelles entrées `DATA_CHECKS` (colonne NULL avant migration ⇒
indisponible) et leur `EXPLAIN QUERY PLAN`.

## 10. Critère de succès du reste-à-faire

- `pytest tests/test_editorial_queue.py` vert, tests existants **non modifiés** ;
- les 19 sujets structurels présents dans `queue/articles.json`, dont 3 sans
  `debloque_par` ;
- `queue_status.py` montre 5 articles jugeables par la couche A sur les 15
  instanciés, et ce chiffre passe à 15 une fois le Lot 15 livré, sans qu'aucune
  ligne de la file ne bouge.
