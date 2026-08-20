# Spec — Lot 5 : publication quotidienne (`publish_next.py`)

Statut : à faire · Dépend de : Lot 3 · PR unique
Rédigée le 2026-08-18, **alignée le même jour** sur le code de la file livré
sur la branche `editorial/file-articles` (voir `specs/Lot 3.md`). Dernier maillon de la chaîne éditoriale : c'est le seul
programme du projet qui écrive sur le dépôt public sans qu'un humain regarde.

## 1. Objectif

`python3 publish_next.py`, lancé par cron à 6 h. Prend le prochain article prêt
et compatible avec le jour, le déplace de `queue/redige/` vers
`site/content/etudes/`, commite, pousse — Vercel déploie, le flux RSS est
régénéré par le build — puis annonce sur Discord.

Un article publié à tort ne se rattrape pas : il est en ligne, il est indexé,
il est dans le RSS. Tout ce lot est donc écrit autour d'une question : **par
quels chemins un chiffre faux pourrait-il arriver en ligne, et lequel de ces
chemins est fermé par un test ?**

## 2. Contraintes absolues

1. **Aucun LLM dans ce chemin.** Ni appel, ni import, ni clé lue. C'est ce qui
   rend la publication automatique sûre : un programme déterministe échoue de
   façon prévisible, un modèle échoue de façon plausible. Vérifié par un test
   (§9.1), pas par une intention.
2. **La couche A est relancée juste avant le commit**, sur les fichiers à leur
   emplacement définitif. L'article a déjà passé A et B au Lot 4, mais entre
   les deux le JSON de données a pu être ré-exporté. C'est le dernier filet, et
   c'est le même juge déterministe qu'au départ.
3. **Refus d'un article dont le patch n'est plus courant.** Le patch courant
   est celui de `lolcollector.editorial.DataState` — le plus récent en base.
   Une seule définition de « patch courant » dans toute la chaîne : deux
   définitions concurrentes finiraient par diverger un jour de sortie de patch.
   Data Dragon sert de contrôle **secondaire, qui ne peut que refuser** (§5).
4. **Un article par exécution, au plus.** Un rattrapage qui publierait trois
   articles d'un coup viderait la réserve et noierait le lecteur.
5. **Exécution unique.** Verrou `flock` sur `queue/.lock` : deux crons qui se
   chevauchent ne publient pas deux fois.
6. **Le dépôt doit être propre avant de commencer.** Aucune modification non
   commitée sous `site/` ou `queue/`, branche `main`, local à jour avec
   `origin/main`. Sinon on sort sans rien toucher — le rollback du §6 repose
   sur cette précondition.

## 3. Séquence

```
 1. verrou flock            échec -> 4
 2. état du dépôt           sale / mauvaise branche -> 5
 3. déjà publié aujourd'hui -> 0 (idempotent : cron relancé, rien à faire)
 4. patch courant (DataState) aucun patch en base -> 6
 5. sélection du candidat   aucun -> 3
 6. règle du patch          périmé -> statut perime, candidat suivant
 7. données présentes       absentes -> candidat ignoré et journalisé
 8. déplacement queue/redige/<id>/ -> site/content/etudes/<famille>/<slug>/
 9. couche A + meta.json    échec -> rollback, tentative +1, statut a_rediger -> 7
10. commit unique + push    échec -> 7 (commit conservé, poussé au run suivant)
11. statut publie, annonce Discord après délai
```

Les étapes 6 et 7 bouclent sur les candidats suivants ; toutes les autres sont
terminales pour l'exécution.

## 4. Sélection du candidat

Vivier : articles de statut `verifie` dont le `debloque_par` reste satisfait
(Lot 3 §4). Tri déterministe :

1. régime, dans l'ordre `comparatif` → `patch_courant` → `structurel` — du plus
   périssable au moins périssable ;
2. `jour_prefere` croissant, comme la file ;
3. `id` alphabétique (départage, pour que deux exécutions sur le même état
   choisissent le même article).

C'est l'ordre **de publication**, et il ne dépend pas du stock : contrairement
à `selection_order` (Lot 3 §5), qui inverse la priorité quand la réserve
s'épuise pour la reconstituer, publier un structurel en priorité laisserait
périmer un article de patch. Les deux ordres sont différents parce que les deux
questions le sont — « que faut-il écrire d'avance » n'est pas « que faut-il
sortir aujourd'hui ».

« Compatible avec le jour » se réduit à cela plus la règle du patch : la
publication n'a pas de fenêtre propre. La maturité du patch et l'âge minimal
sont des conditions de **rédaction** (Lot 3 §5).

## 5. La règle du patch

L'autorité est `meta.json.patch_sensitive` de l'article — le champ que le site
lit déjà (`site/lib/etudes.ts`), renseigné par le Lot 4 depuis le régime :
`true` pour `patch_courant` et `comparatif`, `false` pour `structurel`.

- `patch_sensitive: true` et `patch_cible != patch_courant` ⇒ **refus** :
  statut `perime`, ligne d'historique, candidat suivant. Un article périmé
  n'est jamais repêché : sa tier list décrit un patch que plus personne ne
  joue, et la republier serait publier un chiffre faux au sens de la charte.
- `patch_sensitive: false` ⇒ publiable quel que soit le patch courant. Une
  étude structurelle décrit une fenêtre de collecte que son `meta.json`
  annonce ; l'interdire viderait le réservoir tampon de son intérêt. Si son
  patch a plus d'un patch de retard, avertissement journalisé, publication
  quand même.

**Deux sources, une seule autorité.** Le patch courant est
`DataState.patch` (le plus récent en base), la définition qu'emploie déjà toute
la file. Data Dragon vient par-dessus, en contrôle qui **ne peut que refuser** :
s'il est joignable et annonce un patch plus récent que celui de l'article, on
refuse ; s'il est injoignable, on s'en tient à la base. Le cas qui motive ce
garde-fou est concret — collecteur à l'arrêt, la base reste sur 16.16 pendant
que le monde joue 16.17, et sans ce contrôle on publierait une tier list
périmée tous les jours sans jamais s'en apercevoir.

## 6. Déplacement, vérification, commit

**Déplacement.** `queue/redige/<id>/{index.mdx,meta.json}` →
`site/content/etudes/<famille>/<slug>/`. Le répertoire cible **ne doit pas
exister** : s'il existe, c'est un doublon (Lot 3 §2), statut `perime` et
candidat suivant. Les données (`site/data/etudes/<famille>/<slug>/*.json`) sont
déjà en place, déposées par l'export ; leur absence fait ignorer le candidat
sans consommer de tentative — ce n'est pas un défaut de rédaction.

**Vérifications, dans cet ordre :**

1. `python3 scripts/verify_study.py site/content/etudes/<famille>/<slug>` en
   sous-processus, code 0 exigé ;
2. `meta.json` complet : les champs de `REQUIRED_META_FIELDS` de
   `site/lib/etudes.ts` (`title`, `date`, `patch`, `patch_sensitive`,
   `sample_size`, `regions`, `collected_at`), plus `description` et `tags`.
   C'est la cause connue d'échec du build Next.js, et la répliquer en Python
   coûte dix lignes.

`npm run build` n'est **pas** lancé par défaut : quelques minutes de Node sur
une machine à 8 Go qui fait tourner le collecteur, pour une vérification que
Vercel refera de toute façon. Option `PUBLISH_RUN_BUILD=1` pour l'activer si
l'expérience montre d'autres causes d'échec de build.

**Rollback** (échec d'une vérification) : `git reset --hard HEAD` puis
`git clean -fd site/content/etudes/<famille>/<slug>`, et les fichiers sont
remis dans `queue/redige/<id>/`. Sans la précondition §2.6 (dépôt propre), ce
`reset` détruirait du travail non commité : les deux règles ne se séparent pas.
L'article repasse en `a_rediger`, `tentatives` +1, avec la sortie de la couche A
en historique.

**Commit unique.** Un seul commit contient : le contenu ajouté, les données si
elles n'étaient pas encore suivies, `queue/articles.json` mis à jour (statut
`publie`, `publie_le`), et la suppression de `queue/redige/<id>/`. L'état de la
file et l'état du site ne peuvent donc pas diverger : l'idempotence vient de
git, pas d'un fichier d'état parallèle.

Message : `Publication : <titre>` puis, en corps, l'étude, le patch et le
nombre de nombres vérifiés par la couche A.

**Push.** Échec ⇒ code 7, **commit conservé**. L'exécution suivante voit le
local en avance sur `origin/main` (étape 2), pousse d'abord, et ne sélectionne
un nouvel article qu'ensuite. On ne réécrit jamais l'historique d'un dépôt
poussé.

## 7. Annonce

Après un push réussi : attente de `PUBLISH_ANNOUNCE_DELAY` (180 s par défaut,
le temps du déploiement Vercel — l'annonce pointe vers la page en ligne), puis
`python3 scripts/notify_discord.py --study <famille>/<slug>` en sous-processus.

Un échec d'annonce **ne fait pas échouer la publication** (code 0) : l'article
est en ligne, c'est l'essentiel, et `notify_discord.py` est déjà idempotent.
L'historique enregistre `annonce: false` ; `publish_next.py --rattraper-annonces`
relance les annonces manquantes sans rien publier.

Le flux RSS ne demande rien : il est régénéré par le build Vercel.

## 8. Interface et codes de sortie

```
python3 publish_next.py
python3 publish_next.py --dry-run          # tout sauf déplacement, commit, push, annonce
python3 publish_next.py --article <id>     # force un article précis (les règles s'appliquent)
python3 publish_next.py --rattraper-annonces
```

| code | signification |
|---|---|
| 0 | un article publié, ou déjà publié aujourd'hui |
| 3 | rien à publier (réserve vide ou tout invisible) — état à surveiller |
| 4 | une autre exécution tient le verrou |
| 5 | dépôt non publiable (sale, mauvaise branche, en retard sur `origin`) |
| 6 | patch courant indéterminable (aucun patch en base) |
| 7 | échec de publication (couche A, meta, commit ou push) |

Le code 3 est distinct des erreurs : ce n'est pas une panne, c'est une réserve
vide, et la réponse est éditoriale (rédiger), pas technique.

## 9. Tests — `tests/test_publish_next.py`

Dépôt git fixture construit dans `tmp_path` (`git init`, remote « distant » en
dépôt nu local), file et articles fixtures. Aucun réseau : Data Dragon est
injecté.

1. **Aucun LLM** — le test qui porte la contrainte n°8 du brief :
   - analyse AST de `publish_next.py` : aucun import de `anthropic`,
     `review_study`, `batch_write`, `openai` ; aucune occurrence de
     `ANTHROPIC_API_KEY`, `REVIEW_MODEL`, `WRITER_MODEL` ;
   - après `import publish_next`, `sys.modules` ne contient aucun de ces
     modules — le graphe d'import transitif est propre, pas seulement le
     fichier ;
   - le test échoue si un import indirect les ramène un jour.
2. **Patch périmé** : article `patch_sensitive: true` sur 16.15, patch courant
   16.16 ⇒ statut `perime`, rien de commité, candidat suivant essayé.
3. **Structurel** : `patch_sensitive: false` sur un patch ancien ⇒ publié,
   avertissement journalisé.
4. **Patch** : aucun match en base ⇒ code 6, aucun commit ; Data Dragon
   injoignable ⇒ on publie sur la foi de la base (pas de blocage) ; Data Dragon
   annonçant un patch plus récent que l'article ⇒ refus, même si la base est
   d'accord avec l'article.
5. **Ordre de sélection** : trois articles `verifie` (un par régime) ⇒ le
   comparatif est choisi ; à régime égal, le plus ancien ; à date égale, le
   plus petit `id`. Deux exécutions sur le même état choisissent le même.
6. **Invisibilité** : article `verifie` mais dont un `debloque_par` est faux ⇒
   jamais sélectionné.
7. **Couche A rejetante** (fixture aux chiffres faux) ⇒ rien de commité,
   arbre de travail identique à l'état initial, article en `a_rediger`,
   `tentatives` = 1.
8. **`meta.json` incomplet** ⇒ même traitement, avec le champ manquant nommé.
9. **Doublon** : `site/content/etudes/<famille>/<slug>/` déjà présent ⇒
   `perime`, aucun écrasement du contenu existant.
10. **Commit unique** : après publication, un seul commit, contenant le
    contenu, la file mise à jour et la suppression de `queue/redige/<id>/`.
11. **Push en échec** ⇒ code 7, commit local conservé ; exécution suivante :
    pousse d'abord, ne publie pas un deuxième article.
12. **Idempotence** : deuxième exécution le même jour ⇒ code 0, aucun nouveau
    commit.
13. **Verrou** : verrou déjà pris ⇒ code 4, rien fait.
14. **Dépôt sale** ⇒ code 5, modification locale intacte.
15. **`--dry-run`** : aucun fichier déplacé, aucun commit, code de sortie
    reflétant ce qui se serait passé.
16. **Annonce en échec** ⇒ code 0, `annonce: false` en historique,
    `--rattraper-annonces` la rejoue.
17. Suite existante intacte et verte.

## 10. Cron

Sur le Hetzner, utilisateur `aristide` (pas de sudo, donc `crontab -e`) :

```
0 6 * * * flock -n queue/.lock python3 publish_next.py >> logs/publish.log 2>&1
```

`flock` en plus du verrou interne : ceinture et bretelles, et il protège aussi
contre une exécution manuelle lancée pendant le cron. Le journal est rotatif
comme `logs/collector.log`.

Une dépendance à noter : c'est le cron hebdomadaire du Lot 4 qui appelle
`scripts/queue_sync.py` à la détection d'un nouveau patch, et donc qui recharge
la file (15 articles par patch). `publish_next.py` ne crée jamais d'article ; s'il
sort en 3 plusieurs jours d'affilée, c'est en amont qu'il faut regarder.

## 11. Critère de succès du lot

- `pytest tests/test_publish_next.py` vert + suite existante verte ;
- `--dry-run` sur le dépôt réel avec un article de test en `verifie` : sélection
  attendue, couche A verte, aucun fichier touché ;
- publication réelle d'un article : un commit, un push, page en ligne, annonce
  Discord partie ; deuxième exécution le même jour ⇒ code 0 sans rien faire.
