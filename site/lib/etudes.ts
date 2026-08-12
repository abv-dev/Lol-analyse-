import fs from "fs";
import path from "path";

const CONTENT_DIR = path.join(process.cwd(), "content", "etudes");
const DATA_DIR = path.join(process.cwd(), "data", "etudes");

export interface EtudeMeta {
  title: string;
  description: string;
  date: string; // ISO, date de publication
  patch: string; // patch étudié, ex "16.15"
  patch_sensitive: boolean; // pilotera le refresh automatique par patch
  sample_size: number; // nombre de matchs de l'échantillon
  regions: string[]; // régions couvertes
  collected_at: string; // fin de la collecte des données
  tags: string[]; // affichés sur les cartes
}

export interface Etude {
  family: string; // famille d'étude, ex "tierlist"
  patchSlug: string; // patch en slug URL, ex "16-15"
  meta: EtudeMeta;
  source: string; // contenu MDX brut
}

const REQUIRED_META_FIELDS: (keyof EtudeMeta)[] = [
  "title",
  "date",
  "patch",
  "patch_sensitive",
  "sample_size",
  "regions",
  "collected_at",
];

/** "16.15" -> "16-15" (slug URL) et inverse. */
export function patchToSlug(patch: string): string {
  return patch.replace(/\./g, "-");
}
export function slugToPatch(slug: string): string {
  return slug.replace(/-/g, ".");
}

function listDirs(parent: string): string[] {
  if (!fs.existsSync(parent)) return [];
  return fs
    .readdirSync(parent)
    .filter((entry) => fs.statSync(path.join(parent, entry)).isDirectory());
}

/** Familles d'études (content/etudes/[famille]/). */
export function getFamilies(): string[] {
  return listDirs(CONTENT_DIR).sort();
}

/** Versions (slugs de patch) d'une famille, la plus récente en premier. */
export function getVersions(family: string): string[] {
  const numeric = (slug: string) => slug.split("-").map((n) => parseInt(n, 10) || 0);
  return listDirs(path.join(CONTENT_DIR, family)).sort((a, b) => {
    const na = numeric(a);
    const nb = numeric(b);
    for (let i = 0; i < Math.max(na.length, nb.length); i++) {
      if ((nb[i] ?? 0) !== (na[i] ?? 0)) return (nb[i] ?? 0) - (na[i] ?? 0);
    }
    return 0;
  });
}

export function getLatestPatchSlug(family: string): string {
  const versions = getVersions(family);
  if (versions.length === 0) {
    throw new Error(`[etudes] la famille "${family}" n'a aucune version publiée`);
  }
  return versions[0];
}

/**
 * Charge une étude depuis /content/etudes/[famille]/[patch-slug]/.
 * Convention stricte : index.mdx + meta.json obligatoires. Un meta.json
 * absent ou incomplet FAIT ÉCHOUER LE BUILD — c'est voulu : c'est lui qui
 * pilotera le refresh automatique par patch (patch_sensitive).
 */
export function getEtude(family: string, patchSlug: string): Etude {
  const dir = path.join(CONTENT_DIR, family, patchSlug);
  const metaPath = path.join(dir, "meta.json");
  const mdxPath = path.join(dir, "index.mdx");

  if (!fs.existsSync(metaPath)) {
    throw new Error(
      `[etudes] meta.json manquant pour l'étude "${family}/${patchSlug}" (${metaPath}). ` +
        `Chaque étude doit fournir meta.json (patch, patch_sensitive, sample_size, regions…).`
    );
  }
  if (!fs.existsSync(mdxPath)) {
    throw new Error(
      `[etudes] index.mdx manquant pour l'étude "${family}/${patchSlug}" (${mdxPath}).`
    );
  }

  const meta = JSON.parse(fs.readFileSync(metaPath, "utf8")) as EtudeMeta;
  const missing = REQUIRED_META_FIELDS.filter((field) => meta[field] === undefined);
  if (missing.length > 0) {
    throw new Error(
      `[etudes] meta.json de "${family}/${patchSlug}" incomplet, champs manquants : ${missing.join(", ")}`
    );
  }

  return { family, patchSlug, meta, source: fs.readFileSync(mdxPath, "utf8") };
}

/** Toutes les études (toutes familles × versions), plus récentes en premier. */
export function getAllEtudes(): Etude[] {
  return getFamilies()
    .flatMap((family) => getVersions(family).map((slug) => getEtude(family, slug)))
    .sort((a, b) => (a.meta.date < b.meta.date ? 1 : -1));
}

/** Lit un JSON de données depuis /data/etudes/[famille]/[patch-slug]/[file]. */
export function readStudyData<T = unknown>(family: string, patchSlug: string, file: string): T {
  const filePath = path.join(DATA_DIR, family, patchSlug, file);
  if (!fs.existsSync(filePath)) {
    throw new Error(
      `[etudes] données manquantes : ${filePath} (référencé par "${family}/${patchSlug}")`
    );
  }
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
}

/**
 * Comme readStudyData, mais rend null si le fichier n'existe pas.
 * Pour les données ajoutées après coup (tierlist-roles.json) : une étude
 * publiée avant leur existence doit continuer à se construire.
 */
export function readStudyDataOptional<T = unknown>(
  family: string,
  patchSlug: string,
  file: string
): T | null {
  const filePath = path.join(DATA_DIR, family, patchSlug, file);
  if (!fs.existsSync(filePath)) return null;
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
}
