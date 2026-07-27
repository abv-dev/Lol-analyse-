import fs from "fs";
import path from "path";

const CONTENT_DIR = path.join(process.cwd(), "content", "etudes");
const DATA_DIR = path.join(process.cwd(), "data", "etudes");

export interface EtudeMeta {
  title: string;
  description: string;
  date: string; // ISO, date de publication
  patch: string; // patch étudié, ex "16.14"
  patch_sensitive: boolean; // pilotera le refresh automatique par patch
  sample_size: number; // nombre de matchs de l'échantillon
  regions: string[]; // régions couvertes, ex ["europe", "asia"]
  collected_at: string; // date de collecte des données
  tags: string[]; // rank/région/rôle, affichés sur les cartes
}

export interface Etude {
  slug: string;
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

/**
 * Charge une étude depuis /content/etudes/[slug]/.
 * Convention stricte : index.mdx + meta.json obligatoires. Un meta.json
 * absent ou incomplet FAIT ÉCHOUER LE BUILD — c'est voulu : c'est lui qui
 * pilotera le refresh automatique par patch (patch_sensitive).
 */
export function getEtude(slug: string): Etude {
  const dir = path.join(CONTENT_DIR, slug);
  const metaPath = path.join(dir, "meta.json");
  const mdxPath = path.join(dir, "index.mdx");

  if (!fs.existsSync(metaPath)) {
    throw new Error(
      `[etudes] meta.json manquant pour l'étude "${slug}" (${metaPath}). ` +
        `Chaque étude doit fournir meta.json (patch, patch_sensitive, sample_size, regions…).`
    );
  }
  if (!fs.existsSync(mdxPath)) {
    throw new Error(`[etudes] index.mdx manquant pour l'étude "${slug}" (${mdxPath}).`);
  }

  const meta = JSON.parse(fs.readFileSync(metaPath, "utf8")) as EtudeMeta;
  const missing = REQUIRED_META_FIELDS.filter((field) => meta[field] === undefined);
  if (missing.length > 0) {
    throw new Error(
      `[etudes] meta.json de "${slug}" incomplet, champs manquants : ${missing.join(", ")}`
    );
  }

  return { slug, meta, source: fs.readFileSync(mdxPath, "utf8") };
}

/** Toutes les études, les plus récentes en premier. */
export function getAllEtudes(): Etude[] {
  if (!fs.existsSync(CONTENT_DIR)) return [];
  return fs
    .readdirSync(CONTENT_DIR)
    .filter((entry) => fs.statSync(path.join(CONTENT_DIR, entry)).isDirectory())
    .map(getEtude)
    .sort((a, b) => (a.meta.date < b.meta.date ? 1 : -1));
}

/** Lit un JSON de données d'étude depuis /data/etudes/[slug]/[file]. */
export function readStudyData<T = unknown>(slug: string, file: string): T {
  const filePath = path.join(DATA_DIR, slug, file);
  if (!fs.existsSync(filePath)) {
    throw new Error(`[etudes] données manquantes : ${filePath} (référencé par l'étude "${slug}")`);
  }
  return JSON.parse(fs.readFileSync(filePath, "utf8")) as T;
}
