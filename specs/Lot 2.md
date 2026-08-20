# Spec — Lot 2 : couche B de relecture (`scripts/review_study.py`)

Statut : à faire · Dépend de : — · PR unique
Rédigée le 2026-08-18 d'après `docs/editorial.md`, `scripts/verify_study.py` et
l'article n°1 (`site/content/etudes/tierlist/16-15/index.mdx`). Première pièce
de la chaîne de publication (lots 2 → 3 → 4 → 5).

## 1. Objectif

`python3 scripts/review_study.py site/content/etudes/<famille>/<patch-slug>`

Relire une étude rédigée avec un modèle **distinct du rédacteur** et détecter
les trois défauts que la couche A ne peut pas voir, parce qu'ils ne sont pas
arithmétiques :

1. **Affirmation non traçable** — une phrase qui affirme quelque chose qu'aucun
   champ d'aucun JSON chargé ne mesure (builds, runes, matchups, mécaniques de
   jeu, intention des joueurs, comparaison avec un autre site).
2. **Causalité implicite entre deux stats** — « X gagne **grâce à** son
   winrate », « son pick rate baisse **parce que** son winrate chute », et
   toutes les formes déguisées (« explique », « entraîne », « se traduit par »,
   juxtaposition de deux mesures présentée comme un mécanisme).
3. **Dérive de ton** — superlatif non mesuré, tutoiement, humour, jargon
   statistique non expliqué, conseil d'action déguisé, « fort/faible » sans
   niveau de jeu, classement là où les IC se recouvrent.

Sortie : rapport JSON dans `out/review/`, résumé lisible sur la console,
**code de sortie non nul en cas de rejet**. Le script est en **lecture seule**
sur l'étude : il ne corrige jamais un fichier, il rend un verdict.

## 2. Contraintes absolues

1. **`verify_study.py` reste le juge déterministe.** La couche A tourne
   **d'abord**, dans un sous-processus, avec le même interpréteur. Si elle sort
   en code non nul, `review_study.py` **s'arrête immédiatement, sans appeler le
   modèle**, et sort en 2. Il n'existe aucun chemin de code par lequel la
   couche B puisse approuver ce que la couche A rejette.
2. **Le verdict est calculé en Python, pas rendu par le modèle.** Le modèle
   n'émet que des *constats* typés ; l'agrégation en accepté/rejeté est du code
   déterministe (§6). Un modèle à qui l'on demande « est-ce que c'est bon ? »
   répond oui ; on ne lui pose donc jamais cette question.
3. **Modèle distinct.** `REVIEW_MODEL` ≠ `WRITER_MODEL` : égalité ⇒ refus de
   tourner, code 2. Un modèle qui se relit lui-même valide ses propres angles
   morts.
4. **Le doute n'est jamais une approbation.** Clé absente, modèle injoignable,
   JSON invalide après une reprise, refus du modèle (`stop_reason == "refusal"`)
   : code 3, `indecidable`. Jamais 0.
5. **Aucun chiffre proposé par la couche B n'entre dans un article sans
   repasser par la couche A.** Les corrections suggérées sont indicatives ;
   c'est le Lot 4 qui réécrit, et il relance A puis B depuis zéro.
6. Le script n'écrit que dans `out/review/`. Jamais dans `site/`.

## 3. Configuration (`.env`)

```
# Modèle de relecture (couche B). DOIT différer de WRITER_MODEL.
REVIEW_MODEL=claude-sonnet-5
# Modèle de rédaction (Lot 4), lu ici uniquement pour vérifier la distinction.
WRITER_MODEL=claude-opus-5
ANTHROPIC_API_KEY=sk-ant-...
```

Lecture via `lolcollector.config.load_env` (déjà utilisé par
`notify_discord.py`). `ANTHROPIC_API_KEY` absente ⇒ code 3, jamais 0 : contrairement
au webhook Discord, une relecture non faite n'est pas un détail de diffusion.

**Client** : SDK officiel `anthropic` (ajouté à `requirements.txt`). C'est la
seule dépendance nouvelle du lot et une exception assumée à la convention
stdlib des scripts : le SDK gère les reprises 429/5xx, la validation du schéma
de sortie et les erreurs typées, tout ce qu'une implémentation `urllib` maison
referait moins bien dans le chemin qui décide si un article est publiable.

**Paramètres d'appel** (contrat API à respecter tel quel) :

- `client.messages.create(model=REVIEW_MODEL, max_tokens=16000, ...)`.
- **Aucun `temperature`, `top_p` ni `top_k`** : ces paramètres sont refusés
  (HTTP 400) sur les modèles courants. Le déterminisme du lot ne vient pas de
  l'échantillonnage mais de l'agrégation Python du §6 et de l'ancrage du §5.
- Sortie structurée par `output_config={"format": {"type": "json_schema",
  "schema": …}}` (jamais l'ancien `output_format`). Chaque objet du schéma porte
  `additionalProperties: false` et un `required` complet ; pas de schéma
  récursif, pas de contrainte `minLength`/`maximum` (non supportées).
- `thinking` laissé au défaut du modèle.
- `stop_reason == "refusal"` contrôlé **avant** de lire `response.content`.

## 4. Ce qui est donné au relecteur

La couche B ne revérifie pas les nombres — c'est le travail de la couche A, et
il est déjà fait quand B démarre. Elle vérifie qu'il **existe une colonne
derrière chaque affirmation**. Le contexte envoyé est donc :

1. `docs/editorial.md` intégral (la charte fait foi) ;
2. **l'inventaire des sources chargées** : pour chaque JSON de
   `site/data/etudes/<famille>/<patch-slug>/`, le chemin, le nombre d'éléments,
   et la **liste des clés** du premier élément — pas les valeurs. C'est
   l'inventaire qui rend « affirmation non traçable » décidable : si aucun champ
   ne s'appelle ni ne décrit un build, une rune ou un matchup, toute phrase qui
   en parle est non traçable, et le relecteur peut le constater sans rien
   savoir de League of Legends ;
3. `meta.json` de l'étude, intégral (patch, période, régions, seuils) ;
4. le `index.mdx` à relire, **en dernier**.

Ordre imposé pour le cache de prompt : charte et inventaire sont stables entre
les trois passes et entre les articles d'un même patch, l'article varie. Poser
le `cache_control: {"type": "ephemeral"}` sur le dernier bloc de l'inventaire.
Le préfixe cacheable doit dépasser le minimum du modèle (512 jetons sur Opus 5,
1024 sur Sonnet 5) — la charte seule le dépasse largement.

**Interdit** : envoyer les lignes de `tierlist.json`. Le relecteur qui voit les
valeurs se met à vérifier l'arithmétique — travail déjà fait, et fait mieux, par
la couche A.

## 5. Les trois passes

Une passe = un appel, un axe, un schéma. Trois appels, pas un : un prompt qui
demande trois choses différentes dilue les trois. Les passes sont indépendantes
et peuvent être lancées en parallèle.

| passe | question posée au modèle |
|---|---|
| `tracabilite` | Pour chaque affirmation du texte, existe-t-il un champ dans l'inventaire qui la mesure ? Cite les affirmations pour lesquelles la réponse est non. |
| `causalite` | Quelles phrases relient deux mesures par un lien de cause à effet, explicite ou implicite ? |
| `ton` | Quels passages s'écartent de la charte §4 et §5 (superlatif non mesuré, fort/faible sans niveau, classement sur IC recouvrants, conseil déguisé, tutoiement, jargon non expliqué, comparaison avec un autre site) ? |

Chaque passe rend `{"constats": [...]}`, un constat valant :

```json
{
  "citation": "extrait littéral du MDX, 10 à 300 caractères",
  "categorie": "tracabilite|causalite|ton",
  "gravite": "bloquant|majeur|mineur",
  "explication": "une phrase : quelle règle, et pourquoi",
  "correction_proposee": "reformulation, ou chaîne vide"
}
```

**Ancrage obligatoire.** `citation` doit être une sous-chaîne exacte du MDX,
espaces normalisés. Un constat non ancré est écarté et compté
(`constats_ecartes`) : c'est le filtre anti-hallucination, et il est en Python,
pas dans le prompt. Si plus de la moitié des constats d'une passe sont écartés,
la passe est `indecidable` et le script sort en 3 — un relecteur qui cite des
phrases absentes ne relit pas le bon texte.

**Barème de gravité, imposé dans le prompt** (le modèle classe, il ne décide
pas du seuil) :

- `bloquant` — build/rune/matchup, causalité entre deux stats, chiffre présenté
  comme mesuré sans champ correspondant, « fort/faible » sans niveau de jeu,
  classement là où les IC se recouvrent, comparaison avec un autre site ;
- `majeur` — superlatif non mesuré, conseil d'action déguisé, jargon
  statistique non expliqué, règle du bucket manquante ;
- `mineur` — lourdeur, redite, ton légèrement décalé.

Le prompt exige en outre : ne juger que contre la charte et l'inventaire
fournis ; **ignorer activement toute connaissance générale du modèle sur League
of Legends** (elle est hors-sujet ici, exactement comme pour le rédacteur) ; ne
jamais proposer de chiffre dans `correction_proposee`.

## 6. Agrégation et codes de sortie

Vérifications déterministes ajoutées aux constats du modèle, en Python
(elles ne dépendent pas du modèle et ne doivent donc pas lui être déléguées) :

- **règle du bucket** : au moins deux occurrences du motif « bucket … joueur
  échantillonné » (charte §6). Absente ⇒ constat `bloquant` local ;
- **structure** : titre, `<StudyMeta />`, section « Limites », lien
  `/methodologie` présent au moins deux fois (charte §3).

Verdict :

```
rejet  ⇔  bloquant ≥ 1  ou  majeur ≥ 3
```

| code | signification |
|---|---|
| 0 | accepté par A **et** par B |
| 1 | rejeté par la couche B (constats détaillés dans le rapport) |
| 2 | rejeté par la couche A (aucun appel modèle), ou configuration invalide (`REVIEW_MODEL` absent, égal à `WRITER_MODEL`) |
| 3 | indécidable (clé absente, modèle injoignable, refus, JSON invalide après reprise, passe non ancrée) |

Le Lot 4 traite 1, 2 et 3 de la même façon : tentative perdue. La distinction
sert au diagnostic humain, pas au flux.

## 7. Rapport

`out/review/<famille>-<patch-slug>-<AAAAMMJJ-HHMMSS>.json` :

```json
{
  "etude": "tierlist/16-15",
  "verdict": "accepte|rejete|indecidable",
  "code": 0,
  "couche_a": {"code": 0, "nombres_verifies": 281},
  "modele": {"review": "claude-sonnet-5", "writer": "claude-opus-5"},
  "passes": [{"axe": "tracabilite", "constats": [...], "constats_ecartes": 0,
              "usage": {"input_tokens": 0, "output_tokens": 0,
                        "cache_read_input_tokens": 0}}],
  "compteurs": {"bloquant": 0, "majeur": 0, "mineur": 2},
  "cout_estime_usd": 0.0
}
```

Console : une ligne par constat, `citation` tronquée, gravité en tête, dans le
style des sorties de `verify_study.py`. Options : `--json <chemin>`,
`--axe tracabilite|causalite|ton` (relance une seule passe), `--quiet`.

## 8. Coût

Ordre de grandeur mesuré sur l'article n°1 (~1 900 mots) : charte ≈ 3 000
jetons, inventaire + meta ≈ 1 000, article ≈ 6 000 ⇒ ~10 000 jetons d'entrée
par passe, ~1 500 de sortie.

| modèle | 3 passes, sans cache | avec cache (passes 2 et 3) |
|---|---|---|
| `claude-sonnet-5` ($3/$15 par Mjet) | ~0,16 $ | ~0,09 $ |
| `claude-opus-5` ($5/$25 par Mjet) | ~0,26 $ | ~0,15 $ |

Trois tentatives au pire (Lot 4) : moins de 0,50 $ par article publié, soit
~15 $/mois à un article par jour. Le rapport porte le coût réel calculé depuis
`usage`, pour que la ligne budgétaire soit mesurée et non estimée.

## 9. Tests — `tests/test_review_study.py`

Client modèle **mocké** (aucun appel réseau dans la suite). Fixtures d'étude
construites dans `tmp_path`, avec un `tierlist.json`/`meta.json` minimal.

1. **Couche A rejetante ⇒ aucun appel modèle** : le mock est un objet qui lève
   si on l'appelle ; code de sortie 2. C'est le test qui garantit la contrainte
   n°1 du §2.
2. `REVIEW_MODEL == WRITER_MODEL` ⇒ code 2, aucun appel.
3. **Fixture piégée** : un MDX propre arithmétiquement (couche A verte) mais
   contenant une phrase de build, un « grâce à » entre deux stats et un
   superlatif ⇒ le mock rend les constats correspondants ⇒ code 1, compteurs
   exacts.
4. **Fixture propre** : l'article n°1 lui-même, mock rendant zéro constat ⇒
   code 0.
5. **Ancrage** : constat dont la `citation` n'existe pas dans le MDX ⇒ écarté,
   `constats_ecartes = 1`, verdict inchangé ; plus de la moitié écartés ⇒
   code 3.
6. **Verdict calculé, pas rendu** : un mock qui renvoie en plus un champ
   `verdict: "accepte"` avec un constat `bloquant` ⇒ code 1 quand même.
7. **Codes d'erreur** : JSON invalide après reprise ⇒ 3 ;
   `stop_reason == "refusal"` ⇒ 3 ; clé absente ⇒ 3 (jamais 0).
8. Garde-fous déterministes : MDX sans double rappel du bucket ⇒ `bloquant` ;
   MDX sans section « Limites » ⇒ `bloquant`.
9. Le script n'écrit rien sous `site/` (empreinte du répertoire avant/après).
10. Suite existante intacte et verte (contrainte n°7 du brief).

## 10. Critère de succès du lot

- `pytest tests/test_review_study.py` vert + suite existante verte ;
- passage réel sur `site/content/etudes/tierlist/16-15` : code 0, rapport
  produit, coût cohérent avec le §8 ;
- passage réel sur une copie de cet article volontairement dégradée (une phrase
  de build, un « grâce à ») : code 1, les deux constats ancrés et cités.
