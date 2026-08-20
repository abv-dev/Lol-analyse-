# Spec — Lot 15 : profils de vérification (couche A multi-données)

Statut : à faire · Bloque le Lot 4 · Dépend de : — · PR unique
Rédigée le 2026-08-18. Lot créé après la spécification du Lot 3 : la file
éditoriale a rendu visible que la couche A ne sait juger qu'une seule forme de
données, et que deux tiers des gabarits produisent donc des articles
impubliables.

## 1. Le constat qui motive le lot

`scripts/verify_study.py` est écrit pour une étude et une seule :

- `load_study` exige en dur `tierlist.json` + `meta.json` dans le répertoire de
  données ;
- `StudyValues._build` attend des lignes portant `champion_name`, `bucket`,
  `region`, `games`, `wins`, `bans`, et un `meta` portant `cells`,
  `total_matches`, `min_cell_games`.

Tout article dont les chiffres viennent d'ailleurs échoue en couche A. C'est
le bon sens de la défaillance — un profil inadapté **rejette**, il n'approuve
pas — mais cela rend impubliables 6 des 11 gabarits du Lot 3 (méta par rôle
×5 et répartition des postes, portés par `tierlist-roles.json`) et les 3
comparatifs multi-patch. Cadence réelle : un article tous les trois jours.

Le Lot 15 lève ce plafond sans toucher à la sévérité du juge.

## 2. Objectif

`verify_study.py` accepte plusieurs **profils** de données. Un profil dit :
quels fichiers l'étude charge, quelles entités portent le contexte, et quelles
valeurs sont citables. `StudyValues` devient paramétrable ; la machinerie de
lecture du texte (extraction des nombres, blocs, triplets, contexte) ne bouge
pas.

Trois profils au minimum : `tierlist`, `tierlist-roles`,
`tierlist-multipatch`.

## 3. Contraintes absolues

1. **L'article n°1 reste vert sans aucune modification.** Ni son `index.mdx`,
   ni ses JSON. Référence mesurée le 2026-08-18 :
   `2076 cellules, 786509 matchs, patch 16.15` — **281 nombres vérifiés, 20
   exemptés comme structurels**, code 0. Ces trois chiffres sont le test de
   non-régression du lot (§10.1).
2. **Un profil inconnu échoue bruyamment.** Nom déclaré absent du registre ⇒
   `SystemExit` avec le nom fautif et la liste des profils connus, code 2.
   Jamais de repli silencieux sur un profil voisin.
3. **Aucun assouplissement de la vérification.** La contextualité reste la
   règle : un nombre est comparé aux valeurs des entités nommées dans sa ligne
   (ou son bloc), jamais à l'ensemble du dataset. Un profil qui élargit le jeu
   de valeurs doit resserrer le contexte d'autant — c'est le cœur du §8.
4. **Aucun nom de clé deviné.** Les clés réelles sont relevées au §6–§8 depuis
   `lolcollector/export.py` et les fichiers produits.
5. **Déterminisme.** Deux exécutions sur la même étude produisent la même
   sortie ; aucune valeur ne dépend de l'ordre de parcours d'un `set`.
6. Le script reste **stdlib uniquement** et sans dépendance à `lolcollector` :
   il tourne dans le chemin de publication (Lot 5), il ne doit rien importer
   qui puisse évoluer.

## 4. Déclaration du profil

Le profil se déclare dans le **`meta.json` de contenu** de l'étude
(`site/content/etudes/<famille>/<patch-slug>/meta.json`), champ `profil` :

```json
{ "title": "Méta du poste bot — patch 16.16", "profil": "tierlist-roles", ... }
```

C'est le pendant exact du champ `verifieur` du gabarit (Lot 3 §5.3) : le Lot 4
recopie l'un dans l'autre au moment de rédiger. Le profil décrit la forme des
données citées par **le texte**, pas la forme de ce que l'export a produit ;
il appartient donc à l'article.

**Repli, documenté et bruyant** : `profil` absent ⇒ `tierlist`, avec un
avertissement sur `stderr` (« profil non déclaré, repli sur `tierlist` »). Ce
repli existe pour l'article n°1 et pour lui seul ; il est sûr parce qu'il
échoue du bon côté — le profil `tierlist` est le plus restrictif, un article
qui cite des chiffres de rôles sous ce profil est rejeté, pas approuvé. Le
`meta.json` de données n'est pas consulté pour cette question : il est écrit
par l'export, qui ne sait pas ce que le texte citera.

`--profil <nom>` en ligne de commande force un profil (diagnostic), et l'annonce.

## 5. Anatomie d'un profil

```python
Profil = namedtuple("Profil", (
    "nom",
    "fichiers",        # {"requis": [...], "optionnels": [...]}
    "charger",         # (data_dir, meta_contenu) -> sources
    "construire",      # (sources) -> StudyValues
    "entites",         # (texte, valeurs) -> contexte, cf. §5.1
    "structurels",     # nombres exemptés propres au profil
))
PROFILS = {"tierlist": ..., "tierlist-roles": ..., "tierlist-multipatch": ...}
```

`StudyValues` conserve sa surface actuelle — `allowed_for(contexte)`,
`triplet_matches(contexte, wr, low, high)`, `global_values` — mais le contexte
n'est plus un ensemble de noms de champions : c'est un **contexte typé**,
`{"champions": {...}, "postes": {...}, "patchs": {...}}`. Les profils qui
n'utilisent pas une dimension la laissent vide, et `allowed_for` l'ignore.

### 5.1 Résolution du contexte

Inchangée dans son principe (`iter_blocks` + priorité ligne > bloc > paragraphe
introducteur d'un tableau), généralisée à chaque dimension du profil : une
dimension nommée dans la ligne prime sur la même dimension nommée dans le bloc.
**Une dimension du profil qui n'est nommée nulle part restreint au lieu
d'élargir** : sans poste nommé, seules les valeurs tous-rôles sont citables ;
sans patch nommé, seules celles du patch de l'étude. C'est la règle qui empêche
un profil plus riche d'être un profil plus permissif.

## 6. Profil `tierlist` — extraction à l'identique

Comportement actuel, déplacé sans changement de résultat.

- Fichiers requis : `tierlist.json`, `meta.json` (de données).
- Lignes : `champion_id`, `champion_name`, `region`, `bucket`, `games`,
  `wins`, `winrate`, `winrate_ci_low`, `winrate_ci_high`, `pick_rate`,
  `ban_rate`, `bans`, `insufficient_sample`. Comme aujourd'hui, winrate et
  intervalles sont **recalculés** depuis `games`/`wins` par `wilson_ci`, pas
  lus : c'est ce qui fait de la couche A un contrôle et non un écho.
- Découpages citables : global, par bucket, par région. Les cellules fines
  région × bucket restent exclues (échantillons faibles, et elles rendraient la
  vérification permissive) — commentaire déjà présent dans le code, à conserver.
- Valeurs : games, wins, bans, winrate, bornes de Wilson, demi-largeur, pick
  rate, ban rate, écarts de winrate entre buckets, écarts absolus moyens et
  comptes par seuil, comptes de significativité, pick rates cumulés du top 30,
  dénominateurs, `min_cell_games`, paliers de volume (1 000, 3 000, 5 000,
  10 000).
- Entités : champions.

## 7. Profil `tierlist-roles`

- Fichiers requis : `tierlist-roles.json`, `tierlist.json`, `meta.json`.
- Lignes de `tierlist-roles.json`, clés réelles :
  `champion_id`, `region`, `bucket`, `role`, `games`, `wins`. **Ni
  `champion_name`, ni `bans`** — le nom se joint par `champion_id` depuis
  `tierlist.json` (d'où sa présence en fichier requis), et **le profil ne
  publie aucun ban rate par poste** : l'export n'en produit pas, à dessein
  (un ban n'a pas de poste). Toute mention de ban dans un article de rôle est
  donc rejetée par construction.
- `meta.json` porte `roles` (`TOP, JUNGLE, MIDDLE, BOTTOM, UTILITY`) et
  `role_cells`.
- Entités : champions **et postes**.
- Vocabulaire, table explicite dans le profil (la charte §5 impose l'usage
  anglais courant côté texte, l'export utilise les valeurs Riot) :
  `top→TOP`, `jungle→JUNGLE`, `mid|milieu→MIDDLE`, `bot|botlane|adc→BOTTOM`,
  `support|soutien→UTILITY`. Détection insensible à la casse, sur mot entier.
- Découpages citables : par poste, et poste × bucket, poste × région. Le
  dénominateur d'un pick rate par poste reste le nombre de matchs du découpage
  (`meta["cells"]`), jamais le nombre de games du poste : « 12 % des parties
  voient un Yasuo mid », pas « 12 % des mids sont des Yasuo », sauf à ce que
  l'article le dise ainsi — auquel cas le profil publie aussi la part au sein
  du poste, sous une clé distincte.
- Valeurs par (champion, poste, découpage) : games, wins, winrate, bornes de
  Wilson, demi-largeur, pick rate ; plus la répartition des postes d'un
  champion (part de chaque poste dans ses games), qui est le sujet du gabarit
  `postes`.
- **Garde-fou d'agrégat** : la somme des games par poste d'une cellule doit
  être ≤ ses games tous-rôles de `tierlist.json` (l'écart est le
  `team_position` vide, documenté dans l'export). Violation ⇒ `SystemExit` :
  les deux fichiers ne viennent pas du même export.

## 8. Profil `tierlist-multipatch`

- Fichiers requis : `tierlist.json` + `meta.json` du patch de l'étude, plus les
  mêmes fichiers pour chaque patch comparé.
- Les patchs comparés sont **déclarés explicitement** dans le `meta.json` de
  contenu : `"patchs_compares": ["16.16", "16.15"]`. Jamais devinés en listant
  `site/data/etudes/tierlist/` : un verifieur qui va chercher les patchs
  disponibles sur le disque change de verdict quand un export arrive.
  Patch déclaré introuvable ⇒ `SystemExit`.
- Entités : champions **et patchs**. Le patch d'une ligne se détecte sur les
  formes `16.15`, `16,15`, `16-15`.
- **Règle de portée, décisive pour la sévérité** :
  - une valeur brute (winrate, games, pick rate…) d'un patch comparé n'est
    citable que dans une ligne ou un bloc qui **nomme ce patch** ;
  - sans patch nommé, seules les valeurs du patch de l'étude sont citables ;
  - les **écarts entre deux patchs** (winrate, pick rate, ban rate, games) sont
    citables dès que le champion est en contexte : ils sont le sujet même de
    l'article, et un écart n'appartient à aucun des deux patchs.
- Valeurs ajoutées : par champion, écart de winrate et de pick rate entre
  chaque paire de patchs comparés (valeur signée et valeur absolue) ; écart
  absolu moyen sur les champions au-dessus de chaque palier de volume, et
  nombre de champions dépassant 1, 2, 3, 4, 5 points — mêmes seuils que les
  écarts entre buckets du profil `tierlist`, pour que les deux articles
  parlent la même langue.
- Exemptions structurelles : les numéros de **tous** les patchs chargés (le
  profil `tierlist` n'exempte aujourd'hui que celui de son `meta`).

## 9. Ce qui ne change pas

`wilson_ci`, `TOLERANCE` (0,011), `ALLOWED_STRUCTURAL`, `inline_stat_components`
(les `<Stat>` et `<KeyFigure>` remis en texte avant vérification),
`STRIP_PATTERNS`, `NUMBER_RE`, `TRIPLET_RE` et la validation du triplet
« winrate [borne – borne] » comme un tout, `iter_blocks`, l'héritage de contexte
d'un tableau depuis le paragraphe qui l'introduit, le format de sortie console,
`--verbose`, et les codes de sortie 0/1.

Nouveau code de sortie : **2** pour une erreur de profil (inconnu, fichier
requis absent, patch comparé introuvable, garde-fou d'agrégat) — à distinguer du
1, qui reste « un chiffre du texte ne provient pas des données ». Le Lot 5
traite les deux comme un refus de publier.

## 10. Registre machine-lisible

```
python3 scripts/verify_study.py --profils          # un nom par ligne
python3 scripts/verify_study.py --profils --json   # {"profils": ["tierlist", ...]}
```

C'est le contrat que lit le champ `verifieur` de la file (Lot 3 §8.1) : un
gabarit devient visible le jour où son profil apparaît dans cette liste.
Ajouter un profil au registre suffit donc à débloquer les articles
correspondants, sans toucher à la file.

Chiffre à retenir, mesuré sur les 11 gabarits réellement livrés (Lot 3 §8.1) :
**5 des 15 articles instanciés par patch** sont jugeables par la couche A
aujourd'hui. Les 10 autres attendent `tierlist-roles` et
`tierlist-multipatch`.

## 11. Tests — `tests/test_verify_study.py`

Le fichier n'existe pas : la couche A n'a aujourd'hui aucun test, ce qui rend
le golden du §11.1 non négociable avant tout remaniement.

1. **Non-régression, golden** : `verify("site/content/etudes/tierlist/16-15")`
   ⇒ code 0, **281 nombres vérifiés, 20 exemptés**, sur 2076 cellules et
   786 509 matchs. Le test lit l'étude réelle du dépôt, pas une fixture : c'est
   la seule façon de garantir « l'article n°1 reste vert sans modification ».
2. Même étude, `--verbose` : la liste des nombres validés est identique avant
   et après le lot (empreinte de la sortie, capturée dans le test).
3. **Détection contextuelle conservée** (profil `tierlist`) : un winrate
   recopié du mauvais champion ⇒ code 1 ; un triplet dont une borne est
   modifiée ⇒ code 1 ; un chiffre inventé ⇒ code 1.
4. **Profil inconnu** ⇒ code 2, le nom fautif et la liste des profils connus
   dans le message.
5. **Repli** : `meta.json` de contenu sans `profil` ⇒ profil `tierlist`,
   avertissement sur `stderr`, code inchangé.
6. **Profil `tierlist-roles`** : fixture à deux postes pour un même champion ⇒
   le winrate du poste A cité dans un paragraphe qui nomme le poste B est
   rejeté ; le même chiffre dans un paragraphe qui nomme le poste A est
   accepté ; sans poste nommé, seule la valeur tous-rôles passe.
7. `tierlist-roles` : mention d'un ban rate par poste ⇒ code 1 (aucune valeur
   correspondante n'existe) ; garde-fou d'agrégat violé (fixture incohérente)
   ⇒ code 2.
8. **Profil `tierlist-multipatch`** : valeur du patch N−1 citée sans nommer le
   patch ⇒ code 1 ; la même en nommant le patch ⇒ code 0 ; écart entre patchs
   cité sans nommer de patch ⇒ code 0 ; patch déclaré mais absent du disque ⇒
   code 2 ; numéro d'un patch comparé jamais compté comme une mesure.
9. **Déterminisme** : deux exécutions ⇒ sorties identiques.
10. Aucun profil n'est chargé en important le module (le registre est déclaratif,
    pas de lecture de fichier à l'import) ; `--profils --json` rend les trois noms.
11. Suite existante intacte et verte.

## 12. Critère de succès du lot

- `pytest tests/test_verify_study.py` vert + suite existante verte ;
- `python3 scripts/verify_study.py site/content/etudes/tierlist/16-15` : code 0,
  **281 nombres vérifiés, 20 exemptés**, sortie identique à celle d'avant le lot ;
- `--profils --json` rend `tierlist`, `tierlist-roles`, `tierlist-multipatch` ;
- `queue_status.py` (Lot 3) fait passer les articles de rôles et les
  comparatifs de invisible à visible — de 5 à 15 par patch — sans qu'aucune
  ligne de la file ait été modifiée.
