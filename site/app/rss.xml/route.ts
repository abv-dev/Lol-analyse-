import { getAllEtudes, type Etude } from "@/lib/etudes";
import {
  FEED_PATH,
  FEED_TITLE,
  SITE_DESCRIPTION,
  SITE_NAME,
  SITE_URL,
} from "@/lib/site";

// Généré au build comme le reste du site : le flux est un fichier statique,
// aucun rendu à la demande.
export const dynamic = "force-static";

function escapeXml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * "2026-08-11" -> "Tue, 11 Aug 2026 12:00:00 GMT".
 *
 * Midi UTC et non minuit : à minuit, un lecteur situé à l'ouest de Greenwich
 * afficherait la veille comme date de publication.
 */
function rfc822(isoDate: string): string {
  return new Date(`${isoDate}T12:00:00Z`).toUTCString();
}

function itemXml(etude: Etude): string {
  const url = `${SITE_URL}/etudes/${etude.family}/${etude.patchSlug}`;
  return [
    "    <item>",
    `      <title>${escapeXml(etude.meta.title)}</title>`,
    `      <link>${escapeXml(url)}</link>`,
    // L'URL est stable et versionnée par patch : elle fait un guid parfait.
    `      <guid isPermaLink="true">${escapeXml(url)}</guid>`,
    `      <pubDate>${rfc822(etude.meta.date)}</pubDate>`,
    `      <category>Patch ${escapeXml(etude.meta.patch)}</category>`,
    `      <description>${escapeXml(etude.meta.description)}</description>`,
    "    </item>",
  ].join("\n");
}

export function GET(): Response {
  const etudes = getAllEtudes();

  // Date du flux = date de la dernière étude, pas l'heure du build : sinon
  // chaque redéploiement ferait passer le flux pour modifié.
  const lastBuildDate = etudes.length > 0 ? rfc822(etudes[0].meta.date) : undefined;

  const body = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
    "  <channel>",
    `    <title>${escapeXml(FEED_TITLE)}</title>`,
    `    <link>${escapeXml(SITE_URL)}</link>`,
    `    <description>${escapeXml(SITE_DESCRIPTION)}</description>`,
    "    <language>fr-FR</language>",
    `    <generator>${escapeXml(SITE_NAME)}</generator>`,
    `    <atom:link href="${escapeXml(SITE_URL + FEED_PATH)}" rel="self" type="application/rss+xml"/>`,
    ...(lastBuildDate ? [`    <lastBuildDate>${lastBuildDate}</lastBuildDate>`] : []),
    ...etudes.map(itemXml),
    "  </channel>",
    "</rss>",
    "",
  ].join("\n");

  return new Response(body, {
    headers: {
      "Content-Type": "application/rss+xml; charset=utf-8",
      "Cache-Control": "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
