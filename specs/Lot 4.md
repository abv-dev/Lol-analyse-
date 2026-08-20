# Spec — Lot 4 : orchestration de la rédaction (`batch_write.py`)

Statut : à faire · Dépend de : Lot 2, Lot 3, Lot 15 · PR unique
Rédigée le 2026-08-18, **alignée le même jour** sur le code de la file livré
sur la branche `editorial/file-articles` (voir `specs/Lot 3.md`). Dernier lot de la chaîne éditoriale à spécifier ; c'est
lui qui transforme une file de sujets en une réserve d'articles vérifiés.

## 1. Objectif

`python3 batch_write.py`, lancé par cron une fois par semaine. Recharge la file
pour le patch courant, choisit les articles rédigeables, les rédige, les fait
passer par la couche A puis la couche B, et remplit la réserve jusqu'à la cible
de **15 articles `verifie`** (`--stock-min`, défaut du code de la file) —
quinze jours d'avance sur le cron de publication. Le brief disait « ~12 » ;
le code dit 15, et c'est lui qui fait foi.

Trois tentatives par article, puis statut `bloque` et une tâche Todoist. Le lot
n'écrit jamais dans `site/content/` : il produit dans `queue/redige/<id>/`, et
c'est le Lot 5 qui publie.

## 2. Contraintes absolues

1. **Le rédacteur ne reçoit jamais les JSON de données.** Il reçoit un
   **précis** calculé en Python à partir de ces JSON (§5). Les chiffres qu'il
   peut écrire sont donc exactement ceux que le dépôt a calculés, avec la même
   fonction `wilson_ci` que la couche A. Le modèle fait le travail éditorial —
   choisir l'angle, hiérarchiser, écrire — pas l'arithmétique. C'est la
   décision qui rend « des données, pas des impressions » mécanique plutôt
   qu'espérée.
2. **Modèle distinct du relecteur** : `WRITER_MODEL` ≠ `REVIEW_MODEL`, égalité
   ⇒ refus de tourner, code 5. Symétrique du Lot 2 §2.3.
3. **A puis B, dans cet ordre, à chaque tentative.** Un article ne devient
   `verifie` que si les deux sortent en 0. La couche A tourne d'abord : elle est
   déterministe, gratuite, et elle rejette la moitié des défauts sans dépenser
   un jeton de relecture.
4. **La couche B ne partage pas le précis du rédacteur.** Elle reçoit
   l'inventaire des champs (Lot 2 §4), pas les valeurs. Si les deux couches
   lisaient le même précis, une erreur du précis passerait deux fois ; c'est la
   couche A, seule à lire les vraies valeurs, qui l'arrête.
5. **Aucune écriture sous `site/`.** Le lot écrit dans `queue/redige/`,
   `queue/articles.json` et `out/review/`. Rien d'autre.
6. **Verrou `queue/.lock` partagé avec `publish_next.py`** : la file n'a jamais
   deux écrivains.
7. Un échec de rédaction ne dégrade jamais un test ni un seuil. « Corriger le
   texte, jamais assouplir le script » (skill `elolab-redaction`) est repris mot
   pour mot dans le prompt de reprise.

## 3. Séquence

```
 1. verrou (flock, attente 900 s)              échec -> 4
 2. WRITER_MODEL / REVIEW_MODEL / clé API      invalide -> 5
 3. DataState(db) : patch courant EN BASE      aucun patch -> 3
 4. instanciation des gabarits pour ce patch   (scripts/queue_sync.py)
 5. état de la réserve                         cible atteinte -> 0, rien à faire
 6. selection_order(...)                       vide -> 3
 7. pour chaque article, jusqu'à la cible :
      précis -> rédaction -> couche A -> couche B
      succès : statut verifie
      échec  : tentatives +1, constats en historique
      3 échecs : statut bloque + tâche Todoist
 8. rapport final
```

L'étape 3 lit le patch courant **en base** (`DataState`), pas sur Data Dragon :
c'est le patch qu'on peut réellement analyser, pas celui que Riot vient
d'annoncer — décision déjà prise dans `scripts/queue_sync.py`, conservée telle
quelle. L'étape 4 est le retour aux gabarits récurrents : à chaque nouveau
patch, 15 articles rejoignent la file.

## 4. Sélection

Le vivier et l'ordre viennent **entièrement** de
`lolcollector.editorial.selection_order(articles, state, stock, stock_min)` :
`batch_write.py` n'implémente aucune règle de sélection en propre, il consomme
celle qui est déjà testée. Rappel de ce qu'elle applique (Lot 3 §5) :

| régime | rédigeable |
|---|---|
| `patch_courant` | patch mûr : ≥ 3 jours **et** ≥ 50 000 matchs (réglables) |
| `comparatif` | dès le premier jour du patch, avec un patch précédent |
| `structurel` | toujours |

Ordre : `patch_courant` → `comparatif` → `structurel` quand le stock est plein,
**inversé** quand il passe sous `--stock-min` ; départage par `jour_prefere`
puis `id`. Arrêt dès que la réserve atteint la cible, ou que `--max` tentatives
ont été consommées (plafond de coût, 15 par défaut).

Un article en `en_cours` depuis plus d'une heure est considéré abandonné (cron
tué) et repasse en `en_attente` sans consommer de tentative — le statut
`en_cours` est un ajout au code de la file (Lot 3 §8.4).

## 5. Le précis

Chaque gabarit de `queue/templates.json` déclare une fonction `precis`
(champ à ajouter, Lot 3 §8.7). Elle lit les
`donnees_requises` et rend un objet compact — quelques dizaines de valeurs, pas
2 076 cellules :

```json
{
  "patch": "16.16", "total_matches": 812345, "min_cell_games": 200,
  "regions": ["americas", "asia", "europe"],
  "top_pick": [{"champion": "Kai'Sa", "games": 150427, "wins": 74912,
                "winrate": 49.80, "ic": [49.55, 50.05], "pick_rate": 19.13,
                "ban_rate": 4.02, "significatif": false}],
  "top_winrate_volume": [...], "ecarts_buckets": [...],
  "comptes": {"champions": 173, "au_dessus": 71, "en_dessous": 49,
              "indistinguables": 53}
}
```

Règles du précis :

- il est calculé par **le même code** que la couche A pour tout ce qui est
  partagé (`wilson_ci`, recombinaison parties/victoires, jamais de moyenne de
  pourcentages) — sinon les deux couches diverge­raient sur des arrondis ;
- **aucune valeur n'y figure sans son effectif et son intervalle** : le
  rédacteur ne peut pas écrire un winrate nu, il n'en a pas (charte §2) ;
- il porte le drapeau `significatif` par champion, pour que le modèle n'ait
  jamais à décider lui-même si deux intervalles se recouvrent ;
- il est **déterministe** : mêmes JSON, même précis, octet pour octet — c'est
  ce qui rend les tentatives comparables entre elles.

Le précis est joint à l'historique de l'article (empreinte SHA-256, pas le
contenu) : un article rejeté trois fois avec le même précis est un problème de
rédaction ; avec trois précis différents, c'est un problème de données.

## 6. Rédaction

Contexte envoyé, dans cet ordre (les trois premiers blocs sont stables entre
articles d'un même patch, `cache_control` sur le dernier) :

1. `docs/editorial.md` — la charte ;
2. `site/content/etudes/tierlist/16-15/index.mdx` — le modèle de ton et de
   structure, désigné comme tel par le brief §6.3 ;
3. la liste des composants MDX disponibles (`StudyMeta`, `Chapo`, `KeyFigure`,
   `Stat`, `TierTable`, `WinrateChart`) et leurs attributs ;
4. le gabarit : titre, angle, régime, structure attendue ;
5. le précis.

Sortie structurée (`output_config={"format": {"type": "json_schema", ...}}`) :

```json
{"mdx": "<contenu de index.mdx>",
 "meta": {"title": "...", "description": "...", "date": "...", "patch": "...",
          "patch_sensitive": true, "sample_size": 0, "regions": [],
          "collected_at": "...", "tags": [], "profil": "tierlist"}}
```

Le schéma impose les champs de `REQUIRED_META_FIELDS` (`site/lib/etudes.ts`) —
c'est la cause connue d'échec du build Next.js, autant la rendre impossible — et
le champ `profil` du Lot 15, recopié depuis le `verifieur` du gabarit.

Paramètres : `model=WRITER_MODEL`, `max_tokens=16000`. **Aucun `temperature`,
`top_p` ni `top_k`** : refusés (HTTP 400) par les modèles courants.
`stop_reason == "refusal"` contrôlé avant de lire la réponse ⇒ tentative perdue,
jamais un article vide écrit sur le disque.

Les fichiers sont écrits dans `queue/redige/<id>/` **après** validation du
schéma, jamais pendant le flux.

## 7. Les trois tentatives

Une tentative = un cycle complet rédaction → couche A → couche B. Sur échec,
la tentative suivante reçoit en plus :

- couche A : sa sortie brute (les nombres introuvables, leur ligne, leur
  contexte champion) et l'instruction « corriger le texte avec la valeur exacte
  du précis ; ne jamais arrondir pour faire tomber juste » ;
- couche B : les constats (citation, gravité, explication), avec l'instruction
  de réécrire les passages cités sans toucher aux chiffres.

Après trois échecs : statut `bloque`, sortie du flux automatique, et une tâche
Todoist créée avec `milestone_check.create_task` (jeton `TODOIST_API_TOKEN`,
projet résolu par `TODOIST_PROJECT_NAME`, exactement comme les jalons de
collecte). Contenu : « Article bloqué : <titre> » ; description : les trois
motifs d'échec, l'empreinte du précis, le chemin de `queue/redige/<id>/`.
`TODOIST_API_TOKEN` absent ⇒ avertissement, le statut `bloque` est posé quand
même : la file ne dépend pas de la disponibilité de Todoist.

## 8. Coût

Par tentative, ordre de grandeur : charte ~3 000 jetons, article modèle ~6 000,
composants + gabarit ~1 000, précis ~2 000 ⇒ ~12 000 en entrée, ~4 000 en sortie
(un article fait ~2 000 mots).

| étape | modèle | coût |
|---|---|---|
| rédaction | `claude-opus-5` ($5/$25 par Mjet) | ~0,16 $ |
| relecture (Lot 2, 3 passes) | `claude-sonnet-5` ($3/$15) | ~0,16 $ |
| **article accepté du premier coup** | | **~0,32 $** |
| **pire cas, 3 tentatives** | | **~0,96 $** |

Douze articles par semaine : de 4 à 12 $. Le rapport final porte le coût réel
calculé depuis `usage`, cumulé par article et par exécution.

## 9. Interface et codes de sortie

```
python3 batch_write.py
python3 batch_write.py --dry-run        # précis calculé et prompt affiché, aucun appel modèle
python3 batch_write.py --article <id>   # un seul article, les règles s'appliquent
python3 batch_write.py --max 5          # plafond de tentatives pour cette exécution
python3 batch_write.py --cible 12
```

| code | signification |
|---|---|
| 0 | cible atteinte, ou au moins un article vérifié |
| 3 | vivier vide (rien de rédigeable aujourd'hui) |
| 4 | verrou tenu par une autre exécution |
| 5 | configuration invalide (`WRITER_MODEL` absent ou égal à `REVIEW_MODEL`, clé absente) |
| 7 | toutes les tentatives ont échoué techniquement (modèle injoignable) |

Le code 3 n'est pas une panne : c'est une file à recharger, et
`queue_status.py --pourquoi` dit pourquoi.

## 10. Cron

```
0 3 * * 1 flock -w 900 queue/.lock python3 batch_write.py >> logs/write.log 2>&1
```

Lundi 3 h, avant le cron de publication de 6 h et avec attente sur le verrou :
si la publication du jour tient encore la file, la rédaction attend au lieu
d'abandonner pour une semaine.

## 11. Tests — `tests/test_batch_write.py`

Rédacteur et couches **mockés** ; aucun appel réseau, aucune écriture hors
`tmp_path`.

1. **Sélection par régime** : stock plein ⇒ le `patch_courant` est pris en
   premier ; à régime égal, le plus petit `jour_prefere`.
2. **Maturité** : patch de moins de 3 jours, ou sous 50 000 matchs ⇒ aucun
   `patch_courant` rédigeable ; le structurel reste disponible.
3. **Invisibilité** : article dont un `debloque_par` est faux ⇒ jamais
   sélectionné, même en `--article <id>`.
4. **Ordre des couches** : couche A rejetante ⇒ la couche B n'est **pas**
   appelée (mock qui lève) ; tentative comptée.
5. **Trois échecs ⇒ `bloque`** + une tâche Todoist (client Todoist mocké), avec
   les trois motifs dans la description ; `TODOIST_API_TOKEN` absent ⇒ statut
   `bloque` quand même.
6. **Succès** ⇒ statut `verifie`, fichiers présents dans `queue/redige/<id>/`,
   `meta.json` complet, `profil` recopié depuis le gabarit.
7. **Cible** : réserve déjà à `--stock-min` ⇒ code 0 sans aucun appel modèle ;
   `--max` respecté.
7 bis. **La sélection n'est pas réimplémentée** : `batch_write` appelle
   `selection_order` et respecte son ordre, inversion sous le seuil comprise
   (test avec un stock bas ⇒ le structurel passe en tête).
8. **Précis déterministe** : deux calculs sur les mêmes JSON ⇒ octets
   identiques ; toute valeur du précis porte effectif et intervalle (test
   structurel sur le schéma du précis).
9. **`WRITER_MODEL == REVIEW_MODEL`** ⇒ code 5, aucun appel.
10. **Rien sous `site/`** : empreinte du répertoire avant/après.
11. **Verrou abandonné** : article `en_cours` depuis deux heures ⇒ repasse en
    `a_rediger` sans consommer de tentative.
12. **Refus du modèle** (`stop_reason == "refusal"`) ⇒ tentative perdue, aucun
    fichier écrit.
13. Suite existante intacte et verte.

## 12. Critère de succès du lot

- `pytest tests/test_batch_write.py` vert + suite existante verte ;
- `--dry-run` sur la file réelle : sélection et précis conformes, aucun appel ;
- une exécution réelle limitée (`--max 1`) produit un article qui passe A et B
  du premier coup, et que `publish_next.py --dry-run` sélectionne ensuite ;
- le coût rapporté est cohérent avec le §8.
