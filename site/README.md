# EloLab

Site d'études statistiques du jeu classé de League of Legends (FR), sous la
marque **EloLab** — « Le laboratoire de données de la Faille ». Publié depuis
des fichiers, **aucun backend**, tout est généré statiquement (SSG).

Identité : ton sérieux et sobre, thème sombre unique. Conformément aux
guidelines Riot, « LoL » / « League of Legends » n'apparaît jamais dans le
nom ou le logo — uniquement dans les contenus descriptifs.

Stack : Next.js (App Router) · MDX (`next-mdx-remote/rsc`) · Tailwind · recharts.
Thème sombre unique.

## Démarrer

```bash
cd site
npm install
npm run dev        # http://localhost:3000
npm run build      # build statique — échoue si une étude est mal formée
```

## URLs et convention d'étude

Les études sont **versionnées par patch** :

- `/etudes/tierlist/16-15` — version datée, **permanente**
- `/etudes/tierlist` — URL canonique, **redirige vers la plus récente**
- un sélecteur de patch en haut de page navigue dans l'archive

Une étude = un dossier `content/etudes/[famille]/[patch-slug]/` (ex :
`tierlist/16-15`, le slug = patch avec des tirets) contenant
**obligatoirement** :

> ⚠️ **Props MDX : toujours des chaînes.** Écrire `<WinrateChart top="14" />`,
> **jamais** `top={14}` : ce pipeline MDX n'évalue pas les expressions JSX, la
> prop arriverait `undefined` et la valeur par défaut s'appliquerait **sans
> aucune erreur** (le graphique affichait 12 champions sous un titre annonçant
> 14). Les composants convertissent explicitement (`Number(top)`).

- `index.mdx` — le contenu, avec les composants intégrés :
  - `<StudyMeta />` — encart patch / échantillon / régions / date de collecte,
    rempli automatiquement depuis `meta.json`
  - `<TierTable />` — tableau triable (winrate/pick/ban/games) et filtrable
    par bucket de rank et région, depuis `tierlist.json` + `meta.json`
    d'export ; agrégats et intervalles de Wilson recalculés côté client,
    cellules sous le seuil grisées
  - `<WinrateChart top={12} title="…" />` — top des champions les plus joués
    classés par winrate (échantillons suffisants uniquement)
  - `<ChampCard name="Jax" winrate={0.514} … />` — carte ponctuelle
- `meta.json` — métadonnées **requises** :

```json
{
  "title": "…", "description": "…",
  "date": "2026-07-27",
  "patch": "16.14",
  "patch_sensitive": true,
  "sample_size": 48230,
  "regions": ["europe", "asia", "americas"],
  "collected_at": "2026-07-26",
  "tags": ["ADC", "Tous ranks"]
}
```

**Le build échoue** si `meta.json` (ou un de ses champs requis) est absent :
c'est ce contrat qui pilotera plus tard le rafraîchissement automatique des
études `patch_sensitive` à chaque nouveau patch.

Les données chiffrées vivent dans `data/etudes/[famille]/[patch-slug]/`
(`tierlist.json` + `meta.json`) — produites par
[lol-studies-collector](../README.md) (`collector.py export`). La procédure
de publication complète est dans le README racine.

## Pages

- `/` — liste des études (titre, date, patch, tags), plus récentes en premier
- `/etudes/[famille]/[patch]` — étude versionnée ; `/etudes/[famille]` — canonique
- `/methodologie` — sampling par bucket de rank, approximation du tier, limites
- Footer : mention légale Riot obligatoire (texte exact du boilerplate)

## Déploiement Vercel

Le site vit dans le sous-dossier `site/` du repo :

```bash
cd site
npx vercel login
npx vercel --prod
```

Ou en connectant le repo GitHub sur vercel.com : **Root Directory = `site`**,
framework Next.js détecté automatiquement, aucune variable d'environnement
requise.
