/**
 * Identité et URL absolue du site, en un seul endroit.
 *
 * L'URL absolue est nécessaire à trois choses qui doivent rester d'accord :
 * les métadonnées Open Graph (une og:image relative n'est pas résolue par
 * Discord ni Slack), le flux RSS (un lecteur RSS n'a aucun contexte pour
 * résoudre une URL relative) et le script d'annonce Discord.
 */

export const SITE_NAME = "EloLab";
export const SITE_TITLE = "EloLab — le laboratoire de données de la Faille";
export const SITE_DESCRIPTION =
  "Études statistiques du jeu classé de League of Legends : pick, ban et winrate par rank et région, à partir d'un dataset de matchs ranked solo collecté en continu.";

/** URL de production, sans slash final. */
export const SITE_URL =
  process.env.NEXT_PUBLIC_SITE_URL ??
  (process.env.VERCEL_PROJECT_PRODUCTION_URL
    ? `https://${process.env.VERCEL_PROJECT_PRODUCTION_URL}`
    : "https://elolab.vercel.app");

export const FEED_PATH = "/rss.xml";
export const FEED_TITLE = `${SITE_NAME} — nouvelles études`;

/**
 * Déclaration du flux pour le <head>.
 *
 * À réinjecter dans CHAQUE page qui définit son propre `alternates` : Next
 * remplace la clé entière au lieu de la fusionner, donc une page qui pose
 * un `canonical` sans reprendre ceci perd la balise d'auto-découverte.
 */
export const FEED_ALTERNATE = {
  types: {
    "application/rss+xml": [{ url: FEED_PATH, title: FEED_TITLE }],
  },
};
