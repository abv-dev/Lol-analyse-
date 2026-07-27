import type { MDXComponents } from "mdx/types";
import ChampCard from "@/components/ChampCard";
import StudyMeta from "@/components/StudyMeta";
import WinrateChart, { type WinrateDatum } from "@/components/charts/WinrateChart";
import { readStudyData, type Etude } from "@/lib/etudes";

/**
 * Composants disponibles dans les MDX d'étude, liés à l'étude courante :
 * - <StudyMeta /> lit meta.json automatiquement (aucune prop à passer)
 * - <WinrateChart file="winrates.json" /> lit /data/etudes/[slug]/winrates.json
 *   au build (SSG) et hydrate le graphique recharts côté client
 */
export function mdxComponents(etude: Etude): MDXComponents {
  return {
    StudyMeta: () => <StudyMeta meta={etude.meta} />,
    ChampCard,
    WinrateChart: ({ file, title }: { file: string; title?: string }) => {
      const data = readStudyData<WinrateDatum[]>(etude.slug, file);
      return <WinrateChart data={data} title={title} />;
    },
  };
}
