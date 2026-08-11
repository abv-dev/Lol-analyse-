# Charte éditoriale EloLab

Cette charte est la référence pour toute étude publiée sur EloLab. Elle est
extraite de l'étude [tier list 16.15](../site/content/etudes/tierlist/16-15/index.mdx),
qui sert de **modèle de référence** : en cas de doute sur une formulation,
regarder comment elle traite le même cas.

Le garde-fou automatique associé est
[`scripts/verify_study.py`](../scripts/verify_study.py) : il vérifie que
chaque nombre du texte existe réellement dans les données exportées.

---

## 1. La promesse : « des données, pas des impressions »

C'est la raison d'être du site, et la règle qui prime sur toutes les autres.

- **Tout chiffre publié vient du JSON exporté** (`site/data/etudes/[famille]/[patch]/`).
  On le lit, on ne l'estime pas, on ne l'arrondit pas « au plus propre ».
- **Jamais de chiffre issu de la connaissance générale d'un modèle sur League
  of Legends**, d'un autre site, d'un souvenir de patch note ou d'une
  intuition. Un modèle de langage « sait » que tel champion est fort : cette
  connaissance n'a aucune valeur ici et doit être ignorée activement, y
  compris quand elle contredit les données.
- Si un chiffre nécessaire n'est pas dans l'export, **on ne le publie pas** —
  on le calcule proprement en amont (nouvel export, nouvelle métrique), ou on
  écrit qu'on ne l'a pas mesuré.
- Un chiffre qui « paraît faux » se re-vérifie dans le JSON ; s'il est
  confirmé, il se publie tel quel.

**Avant publication** : `python3 scripts/verify_study.py site/content/etudes/<famille>/<patch>`
doit sortir en code 0.

## 2. Incertitude : intervalle de confiance et taille d'échantillon

Chaque chiffre décisif est accompagné de **sa taille d'échantillon** et de
**son intervalle de confiance à 95 %** (Wilson, celui que produit l'export).

- Format : `52,67 % [52,19 – 53,15] sur 41 219 parties`.
- **Un écart dont les intervalles se recouvrent n'est pas un classement.**
  On écrit « indistinguables », « l'écart n'est pas significatif », jamais
  « X est meilleur que Y » ni « X est 3ᵉ ».
- Un intervalle qui contient encore 50 % se décrit comme tel : « ne fait ni
  gagner ni perdre », pas « légèrement au-dessus de 50 % ».
- Les agrégats se recalculent en **recombinant parties et victoires**, jamais
  en moyennant des pourcentages ; l'intervalle se recalcule sur l'échantillon
  résultant.
- Les cellules sous le seuil (`min_cell_games`, 200 par défaut) sont
  **signalées, pas supprimées**, et ne servent jamais de base à une
  affirmation.
- On explique la lecture de l'intervalle **au moins une fois par article**,
  en une phrase concrète (voir la section « Comment lire cette étude » du
  modèle).

## 3. Structure type d'un article

1. **Titre factuel** : `Tier list — patch 16.15`. Objet + patch. Pas
   d'accroche, pas de superlatif, pas de question rhétorique.
2. **`<StudyMeta />`** immédiatement après le titre (patch, échantillon,
   régions, date de collecte).
3. **Chapô « Comment lire cette étude »** : donne le **résultat principal**
   et la clé de lecture des intervalles. Le lecteur qui s'arrête là doit
   repartir avec la conclusion.
4. **Trois sections d'angle** (pas dix), chacune portée par un résultat
   mesuré et titrée par ce résultat — « Les champions les plus joués ne sont
   pas ceux qui gagnent », pas « Analyse des pick rates ».
5. **Tableau interactif** `<TierTable />` : les données complètes, triables
   et filtrables, avec une phrase disant comment les agrégats sont calculés
   et ce que le filtrage fait aux intervalles.
6. **Section « Limites » explicite** : ce que la mesure ne dit pas.
7. **Lien vers la [méthodologie](https://…/methodologie)** au moins une fois
   dans le corps du texte et une fois dans les limites.

Les graphiques (`<WinrateChart />`) illustrent une section d'angle, ils ne la
remplacent pas.

## 4. Ce qu'on ne dit jamais

- **Aucun conseil de build, de rune, d'ordre de sorts ou de matchup** tant
  qu'on ne les a pas mesurés. On ne collecte pas ces dimensions aujourd'hui :
  on n'en dit donc rien, même « à titre indicatif ».
- **Jamais « champion fort » ou « champion faible » sans niveau de jeu.** Un
  champion est fort *en Fer–Bronze*, *en Diamant+*, *sur telle région* — la
  tier list globale est une moyenne qui peut ne décrire personne (cas Yorick
  dans le modèle).
- **Jamais de causalité là où il n'y a que corrélation.** Un winrate élevé
  peut refléter le champion, la population qui le choisit ou les
  compositions dans lesquelles il apparaît. On écrit « les parties où X est
  joué sont gagnées à Y % », pas « X fait gagner ».
- **Jamais de comparaison avec un autre site**, ni de reprise de ses
  chiffres, ni « contrairement à ce qu'on lit ailleurs ». On publie nos
  mesures, point.
- Pas de recommandation d'action déguisée (« à bannir en priorité ») : on
  décrit ce que les bans font aujourd'hui et ce que les winrates disent.
- Pas de superlatif non mesuré (« énorme », « cassé », « incontournable ») ;
  « le meilleur winrate du patch » est acceptable car c'est une mesure.

## 5. Vocabulaire et ton

- **Français**, ton sobre et scientifique. Pas de mascotte, pas d'humour
  forcé, pas de tutoiement du lecteur.
- **Les termes LoL restent en anglais quand c'est l'usage** : winrate, pick
  rate, ban rate, bucket, ladder, patch, solo queue, top/jungle/mid/bot/support.
  On ne traduit pas « winrate » par « taux de victoire ».
- **Les rangs se disent en français** : Fer, Bronze, Argent, Or, Platine,
  Émeraude, Diamant. Les buckets s'écrivent `Fer–Bronze`, `Argent–Or`,
  `Platine–Émeraude`, `Diamant+`.
- **Pas de jargon statistique non expliqué.** « Intervalle de confiance à
  95 % » est introduit par sa lecture concrète ; on évite « p-value »,
  « significativité statistique » brut, « écart-type », « test du χ² ». Si un
  concept est nécessaire, il est expliqué en une phrase, dans le texte.
- Nombres au format français : espace comme séparateur de milliers
  (`150 427`), virgule décimale (`49,80 %`), espace avant `%`.
- Le nom du jeu n'apparaît **jamais dans le nom du site, le logo ou les
  titres** (guidelines Riot) — uniquement dans le corps descriptif.
- La mention légale Riot du pied de page est intouchable (texte exact du
  boilerplate officiel, en anglais).

## 6. La règle du bucket

**Toujours rappeler qu'un bucket est le rang du joueur échantillonné qui a
mené à la partie, pas le rang moyen des dix joueurs.** Match-V5 n'expose pas
le rang des participants.

- À rappeler **au moins deux fois** par article : une fois dans le chapô ou
  la première section qui compare des buckets, une fois dans les limites.
- Formulation type : « Le niveau d'une partie est approximé par le bucket du
  joueur échantillonné (voir la méthodologie). »
- En conséquence, on compare des **populations de parties**, pas des rangs :
  « en Fer–Bronze » veut dire « dans les parties découvertes via un joueur
  Fer ou Bronze ».
- Ne jamais présenter un bucket comme un elo exact, ni convertir un bucket en
  LP, ni parler du « niveau moyen » d'une partie.

## 7. Checklist avant publication

- [ ] `python3 scripts/verify_study.py site/content/etudes/<famille>/<patch>` sort en 0
- [ ] Chaque winrate décisif porte son échantillon et son IC
- [ ] Aucun écart présenté comme un classement quand les IC se recouvrent
- [ ] Aucune mention de build, rune ou matchup
- [ ] Aucun « fort / faible » sans niveau de jeu
- [ ] Aucune formulation causale non justifiée
- [ ] Règle du bucket rappelée au moins deux fois
- [ ] Section « Limites » présente, méthodologie liée
- [ ] `cd site && npm run build` vert
