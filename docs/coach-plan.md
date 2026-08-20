# Plan des lots — EloLab : module Coach + pipeline de rédaction

Source : `docs/brief-cto.md`. Rédigé le 2026-08-12 après lecture du schéma réel
de `data/matches.db` (ouverture en lecture seule, collecteur en marche).
Corrigé le 2026-08-12 : Lot 13 requalifié non-conditionnel, urgence égale au
Lot 0 (décision produit).

**Constats de schéma qui contraignent le plan** (aucun nom de colonne deviné) :

1. `participants` ne contient **aucune colonne de dégâts ni de vision score**.
   Or les dégâts/min portent les croisements de diagnostic du §5.5 du brief
   (« dégâts bas + peu de morts = passivité », « dégâts bas + beaucoup de
   morts = placement ») : sans cette colonne, le Coach perd son diagnostic
   principal. Aucun backfill n'est possible (le JSON brut des matchs n'est pas
   conservé) : à ~157 000 matchs/jour, chaque jour de report est
   définitivement perdu pour ces métriques. D'où le **Lot 13, non
   conditionnel, au même rang d'urgence que le Lot 0** — c'est le seul lot du
   plan dont le coût de report est irréversible. Conséquence pour le Lot 1 :
   les percentiles sur dégâts/vision ne seront calculables que sur les matchs
   postérieurs à la migration (environ un patch de collecte pour retrouver du
   volume).
2. Le filtre « aucun participant à 0 dégât » du Lot 0 est remplacé par un
   proxy AFK documenté dans `specs/Lot 0.md` (il doit fonctionner sur
   l'historique, qui restera sans colonne de dégâts).
3. Aucune colonne « early surrender » : le filtre durée ≥ 20 min le couvre par
   définition (détail dans `specs/Lot 0.md`).
4. « CS à 10 min » n'existe aujourd'hui que via `timeline_frames`
   (échantillonnage 0,10) ; le Lot 13 le rapatrie en fin de partie via les
   champs `challenges` de Match-V5 pour les matchs futurs.
5. Casse réelle des champions : `Kaisa` (145), `Galio` (3), `Nilah` (895).
   Régions stockées : `europe`, `asia`, `americas` (la plateforme `euw1`/`kr`/
   `na1` est dans `matches.platform`).

**Règle de blocage** : les lots 0 et 13 sont spécifiés (`specs/Lot 0.md`,
`specs/Lot 13.md`) et lançables immédiatement, en parallèle. Aucun lot en aval
du Lot 0 (lots 1, 8–12) ne sera spécifié avant lecture de `out/coverage/`.
Les lots 2 à 7 n'en dépendent pas et sont spécifiables immédiatement ; les
lots 2, 3, 4, 5 et 15 sont spécifiés (18 août 2026), les lots 6 et 7 restent à
écrire. La liste définitive des métriques et le seuil de
validité des cellules se fixent après le Lot 0, pas avant.

**Décisions post-Lot 0 (13 août 2026, actées dans le brief §5.3–5.4)** :
seuil de cellule `n_puuid_ge5 ≥ 30`, dégradation en trois étages ; Niveau 2 =
4 métriques par game (seuil propre : ≥ 200 games-timeline propres/cellule ;
« morts ≥ 3 assistants » repassée de `debloque_par` en v1 le 14 août, portée
par `assisting_ids` du Lot 14), 2 métriques en `debloque_par`. La couverture timeline ~2 % de 16.15 est un
artefact historique (collecte active depuis le 11 août) ; régime permanent
mesuré à 10 % dès 16.16. **Rétro-collecte `backfill-timelines` 16.15/16.14 :
optionnelle, non lancée** — ce n'est pas un lot, c'est une commande existante ;
ne la lancer que pour des études structurelles Timeline sur ces patchs (bug
connu à corriger avant : tri lexicographique des patchs dans
`lolcollector/backfill.py::candidates`, signalé le 13 août).

**Lot 15, créé le 18 août 2026 pendant la spécification du Lot 3** :
`scripts/verify_study.py` ne sait juger qu'une seule forme de données
(`tierlist.json` en dur dans `load_study`, six clés attendues par
`StudyValues`). Six des onze gabarits de la file (méta par rôle ×5, répartition
des postes) et les trois comparatifs multi-patch produisent donc des articles
impubliables : cadence réelle d'un article tous les trois jours au lieu d'un
par jour. Le Lot 15 lève ce plafond et passe en **dépendance dure du Lot 4** —
sans lui, `batch_write.py` alimenterait une file dont deux tiers des gabarits
ne peuvent pas être vérifiés.

**Vérifications d'accès faites pour le Lot 1** (base réelle, 14 août) : index
`idx_tl_events_match_type (match_id, type)`, `idx_tl_frames_match_minute
(match_id, minute)`, `idx_participants_match` présents ; l'ordre d'insertion
des `participants` d'un match (rang par `rowid`) est égal au `participantId`
Riot — vérifié contre `info.participants` de trois timelines réelles. Le
Lot 14 transforme cette dérivation en colonne explicite.

## Lot 0 — Mesure de couverture
Objectif : scripts/coverage_check.py, lecture seule, mesure les effectifs par cellule
Critère : le script tourne, produit out/coverage/*.json, aucun EXPLAIN avec scan de participants
Dépend de : —
État : livré (2026-08-13, branche lot-0/coverage-check)

## Lot 13 — Extension du collecteur : dégâts, vision et métriques de fin de partie
Objectif : migration additive sur participants (dégâts aux champions, dégâts subis, vision score, pinks achetés, temps passé mort, CS avant 10 min via challenges, soins et boucliers sur alliés), parsing Match-V5 dans store_match, migration idempotente via le mécanisme MIGRATIONS existant, matchs antérieurs à NULL (précédent horde_kills), application encadrée par stop.sh/start.sh
Critère : pytest tests/test_collector_schema.py vert (migration idempotente sur base fixture à l'ancien schéma, parsing d'un payload Match-V5 réel vers les nouvelles colonnes, NULL — pas 0 — quand un champ est absent) + suite de tests existante intacte et verte
Dépend de : —
État : livré (en production depuis le 2026-08-13 — 100 % des insertions récentes portent dégâts et CS@10)

## Lot 14 — Extension du collecteur : participant_id, achats légendaires, assistants des kills
Objectif : colonne participants.participant_id (parsing Match-V5, backfill historique par rang d'insertion validé, migration via le mécanisme MIGRATIONS, application encadrée par stop.sh/start.sh) ; table item_events (achats et annulations d'objets légendaires complétés extraits des timelines, filtre par liste Data Dragon versionnée et stockée en base) ; colonne timeline_events.assisting_ids sur les CHAMPION_KILL (chaîne vide = zéro assistant, NULL = pré-migration) ; coût mesuré ~31 Mo/jour au rythme actuel
Critère : pytest tests/test_item_events.py vert (migration idempotente, backfill participant_id égal à l'ordre du payload sur fixture, parsing d'une timeline réelle vers item_events filtrés et assisting_ids, liste légendaire versionnée) + suite existante intacte et verte
Dépend de : —
État : spécifié (specs/Lot 14.md)

## Lot 1 — Export benchmarks
Objectif : commande `export --study benchmarks` produisant par cellule champion × team_position × bucket × région (+ étage ALL) : Niveau 1 par joueur (8 métriques, seuil n_puuid_ge5 ≥ 30), Niveau 2 par game (4 métriques timeline, seuil ≥ 200 games-timeline propres), répartition des postes pour l'étage 3, effectifs et IC systématiques, flags de couverture par étage
Critère : pytest tests/test_benchmarks.py vert + EXPLAIN sans scan de participants
Dépend de : Lot 0, Lot 14
État : spécifié (specs/Lot 1.md)

## Lot 2 — Couche B de relecture
Objectif : scripts/review_study.py, relecture par un modèle distinct configuré via REVIEW_MODEL en .env, détecte affirmation non traçable, causalité implicite et dérive de ton, code de sortie non nul en cas de rejet
Critère : pytest tests/test_review_study.py vert (client modèle mocké : parsing du verdict, codes de sortie, fixture piégée rejetée, fixture propre acceptée)
Dépend de : —
État : spécifié (specs/Lot 2.md)

## Lot 3 — File éditoriale
Objectif : queue/articles.json (id, slug, famille, titre, angle, regime, donnees_requises, patch_cible, statut, tentatives max 3, debloque_par), queue/templates.json (11 gabarits : 8 patch_courant + 3 comparatifs) et scripts/queue_status.py
Critère : pytest tests/test_queue.py vert (validation de schéma, article invisible tant que debloque_par manque, instanciation des 11 gabarits pour un patch donné)
Dépend de : —
État : spécifié (specs/Lot 3.md)

## Lot 15 — Profils de vérification (couche A multi-données)
Objectif : scripts/verify_study.py accepte plusieurs profils de données déclarés dans le meta.json de contenu (StudyValues paramétrable par les colonnes du profil), au minimum tierlist, tierlist-roles et le comparatif multi-patch ; registre machine-lisible (--profils --json) lu par le prédicat verifieur: de la file ; profil inconnu = échec bruyant, jamais de repli silencieux
Critère : pytest tests/test_verify_study.py vert, dont le golden de non-régression sur l'article n°1 sans modification (281 nombres vérifiés, 20 exemptés, code 0) + suite existante intacte et verte
Dépend de : —
État : spécifié (specs/Lot 15.md)

## Lot 4 — Orchestration de la rédaction
Objectif : batch_write.py, cron hebdomadaire, sélectionne les articles rédigeables selon l'état réel des données et les trois régimes, rédige, passe couche A puis couche B, 3 tentatives max puis statut bloque + tâche Todoist, cible ~12 articles vérifiés en stock
Critère : pytest tests/test_batch_write.py vert (rédacteur et couches mockés : sélection par régime, patch_courant < 3 jours non rédigeable, passage en bloque après 3 échecs)
Dépend de : Lot 2, Lot 3, Lot 15
État : spécifié (specs/Lot 4.md)

## Lot 5 — Publication quotidienne
Objectif : publish_next.py, cron quotidien 6 h, prend le prochain article prêt compatible avec le jour, commit, push, notification Discord + RSS, refuse un article dont le patch n'est plus courant, aucun LLM dans ce chemin
Critère : pytest tests/test_publish_next.py vert (dry-run sur dépôt fixture : refus patch périmé, ordre de sélection, aucune dépendance LLM importée)
Dépend de : Lot 3
État : spécifié (specs/Lot 5.md)

## Lot 6 — Schéma Neon comptes et analyses
Objectif : schéma Postgres Neon avec migrations : comptes clés par puuid (pas le Riot ID), table analyses (snapshot des métriques + axes donnés + date), régions restreintes à EUW/KR/NA
Critère : pytest tests/test_neon_schema.py vert sur une branche Neon de test via DATABASE_URL_TEST (migrations idempotentes, contraintes de région et d'unicité vérifiées)
Dépend de : —
État : à faire

## Lot 7 — Auth par lien magique
Objectif : authentification la plus légère possible par lien magique email, sans Riot Sign-On, sessions liées au compte Neon
Critère : pytest tests/test_auth.py vert (envoi mocké : émission, expiration et usage unique du lien, session créée)
Dépend de : Lot 6
État : à faire

## Lot 8 — File d'appels Riot priorisée
Objectif : file d'appels Riot partagée donnant priorité aux requêtes utilisateur sur le collecteur, sous les mêmes limites de clé (18 req/s, 84 req/2 min par région), architecture de coordination Hetzner/Vercel à trancher en spec
Critère : pytest tests/test_riot_queue.py vert (sous contention simulée, les requêtes utilisateur passent avant le collecteur et le budget de clé n'est jamais dépassé)
Dépend de : Lot 0
État : à faire

## Lot 9 — Endpoint d'analyse
Objectif : Vercel Function d'analyse : résolution du joueur, récupération à la demande de ~10 games + timelines via la file priorisée, calcul des métriques, comparaison aux percentiles du Lot 1, refus explicite hors EUW/KR/NA, dégradation de cellule affichée
Critère : pytest tests/test_analyze_endpoint.py vert (API Riot mockée : réponse complète, refus de région, repli toutes-régions déclenché sous le seuil)
Dépend de : Lot 1, Lot 6, Lot 8
État : à faire

## Lot 10 — UI de comparaison
Objectif : page Coach du site : tableau trois colonnes (joueur, P90 « profil de référence » de son élo, médiane de l'élo au-dessus), effectifs + IC affichés, dégradation de cellule annoncée explicitement, jamais « le meilleur X »
Critère : build Next.js vert + tests de composants (vitest) : trois colonnes rendues, mention de dégradation présente, libellé « profil de référence »/« top 10 % » vérifié
Dépend de : Lot 1, Lot 9
État : à faire

## Lot 11 — Moteur de conseils branché sur percentiles
Objectif : brancher la knowledge base existante de lol-live-coach (barèmes, table signal → seuil → concept → conseil, priorités Fer/Bronze, règles de rédaction) sur les percentiles réels, sans la dupliquer ; règles héritées : 3 conseils max priorisés, constat horodaté → cause → action vérifiable, récidive signalée, point positif final, ≤ 10 lignes ; diagnostic « fondamentaux propres » implémenté comme cas à part
Critère : pytest tests/test_advice_engine.py vert (golden tests : profils synthétiques → conseils attendus, ≤ 3 conseils, cas fondamentaux propres déclenché)
Dépend de : Lot 1, Lot 9
État : à faire

## Lot 12 — Suivi longitudinal
Objectif : historique des analyses par compte : snapshots comparés entre deux analyses, détection de récidive alimentant le moteur de conseils, restitution de la progression « on mesure, tu testes, on remesure »
Critère : pytest tests/test_history.py vert (deux snapshots fixtures → récidive détectée, progression calculée, dates conservées)
Dépend de : Lot 7, Lot 11
État : à faire
