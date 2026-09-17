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

/** Page de soutien. Surchargeable par NEXT_PUBLIC_KOFI_URL. */
export const KOFI_URL = process.env.NEXT_PUBLIC_KOFI_URL ?? "https://ko-fi.com/elolab";

/** Code source du collecteur, du pipeline d'export et du site. */
export const REPO_URL = "https://github.com/abv-dev/Lol-analyse-";

export const OG_IMAGE = { url: "/og.png", width: 1200, height: 630, alt: SITE_NAME };

/**
 * Métadonnées complètes d'une page statique.
 *
 * Next remplace `openGraph`, `twitter` et `alternates` au lieu de les
 * fusionner avec ceux du layout : une page qui ne pose que `title` et
 * `description` serait partagée sur Discord ou X avec le titre du site.
 */
export function pageMetadata({
  title,
  description,
  path,
}: {
  title: string;
  description: string;
  path: string;
}) {
  const fullTitle = `${title} — ${SITE_NAME}`;
  return {
    title,
    description,
    alternates: { canonical: path, ...FEED_ALTERNATE },
    openGraph: {
      type: "website" as const,
      siteName: SITE_NAME,
      locale: "fr_FR",
      title: fullTitle,
      description,
      url: path,
      images: [OG_IMAGE],
    },
    twitter: {
      card: "summary_large_image" as const,
      title: fullTitle,
      description,
      images: [OG_IMAGE.url],
    },
  };
}
