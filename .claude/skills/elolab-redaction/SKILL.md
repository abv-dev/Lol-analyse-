---
name: elolab-redaction
description: Rédiger, relire ou modifier une étude statistique EloLab (site/content/etudes/**). Applique la charte éditoriale du projet — chiffres issus exclusivement des JSON exportés, intervalles de confiance et tailles d'échantillon systématiques, structure d'article imposée, vocabulaire et interdits. Utiliser dès qu'on écrit ou édite un index.mdx d'étude, qu'on publie une tier list pour un nouveau patch, ou qu'on relit une analyse avant publication.
---

# Rédaction d'une étude EloLab

## Avant d'écrire

1. **Lire la charte** : [`docs/editorial.md`](../../../docs/editorial.md) — elle
   fait foi, ce fichier n'en est que le mode d'emploi.
2. **Lire l'étude de référence** :
   `site/content/etudes/tierlist/16-15/index.mdx`. En cas de doute sur une
   formulation, reproduire sa façon de traiter le même cas.
3. **Lire les données** : `site/data/etudes/<famille>/<patch>/tierlist.json`
   et `meta.json`. Toujours calculer les agrégats depuis ces fichiers (script
   Python jetable), **jamais** de mémoire.

## Les cinq règles qui ne se négocient pas

1. **Aucun chiffre qui ne vienne du JSON.** Ni estimation, ni arrondi
   « propre », ni connaissance générale du modèle sur League of Legends. Cette
   connaissance est hors-sujet et doit être activement ignorée, même quand
   elle contredit les données.
2. **Chaque chiffre décisif porte sa taille d'échantillon et son IC à 95 %** :
   `52,67 % [52,19 – 53,15] sur 41 219 parties`.
3. **IC qui se recouvrent = non significatif**, jamais un classement. Un IC
   qui contient 50 % : « ne fait ni gagner ni perdre ».
4. **Jamais** de conseil de build/rune/matchup (non mesurés), de « champion
   fort/faible » sans niveau de jeu, de causalité sans preuve, de comparaison
   avec un autre site.
5. **La règle du bucket** rappelée au moins deux fois : un bucket est le rang
   du **joueur échantillonné**, pas le rang moyen de la partie.

## Structure d'un article

```
# <Objet> — patch <X.Y>          titre factuel, sans accroche
<StudyMeta />
## Comment lire cette étude      chapô : résultat principal + lecture des IC
## <Angle 1 titré par le résultat>
## <Angle 2>
## <Angle 3>
## Le tableau complet            <TierTable /> + phrase sur les agrégats
## Limites                       ce que la mesure ne dit pas
```

Trois sections d'angle, pas dix. Chaque titre énonce un résultat (« Les
champions les plus joués ne sont pas ceux qui gagnent »), pas un thème
(« Analyse des pick rates »). Lien vers `/methodologie` dans le corps et dans
les limites.

## Vocabulaire

Français sobre. Termes LoL en anglais quand c'est l'usage (winrate, pick
rate, ban rate, bucket, ladder, solo queue) ; rangs en français (Fer–Bronze,
Argent–Or, Platine–Émeraude, Diamant+). Pas de jargon statistique non
expliqué. Nombres à la française : `150 427`, `49,80 %`.

## Vérification obligatoire avant de rendre la main

```bash
python3 scripts/verify_study.py site/content/etudes/<famille>/<patch>
cd site && npm run build
```

Le premier **doit sortir en code 0** : il extrait chaque nombre du MDX et le
vérifie contre le JSON, en tenant compte du champion dont parle le paragraphe
(un winrate recopié du mauvais champion échoue). S'il signale un nombre :
**corriger le texte**, jamais assouplir le script — un « environ 100 000
parties » se réécrit avec la valeur exacte du JSON.

Puis dérouler la checklist en fin de `docs/editorial.md`.

## Publier un nouveau patch

La procédure complète (export → dossier → contenu → build → PR → Vercel) est
dans le README racine, section « Publication d'une étude EloLab ». En
résumé : `collector.py export` sur le serveur, JSON déposés dans
`site/data/etudes/<famille>/<patch-slug>/`, contenu dans
`site/content/etudes/<famille>/<patch-slug>/`, `meta.json` complet sous peine
d'échec du build.
