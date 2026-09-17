import type { MetadataRoute } from "next";
import { getAllEtudes } from "@/lib/etudes";
import { SITE_URL } from "@/lib/site";

export default function sitemap(): MetadataRoute.Sitemap {
  const pages = ["", "/methodologie", "/a-propos", "/mentions-legales"].map((path) => ({
    url: `${SITE_URL}${path}`,
  }));
  const etudes = getAllEtudes().map((etude) => ({
    url: `${SITE_URL}/etudes/${etude.family}/${etude.patchSlug}`,
    lastModified: etude.meta.date,
  }));
  return [...pages, ...etudes];
}
