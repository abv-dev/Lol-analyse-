import type { MDXComponents } from "mdx/types";
import ChampCard from "@/components/ChampCard";
import KeyFigure from "@/components/KeyFigure";
import Stat from "@/components/Stat";
import StudyMeta from "@/components/StudyMeta";
import TierTable from "@/components/TierTable";
import WinrateChart, { type WinrateDatum } from "@/components/charts/WinrateChart";
import { readStudyData, type Etude } from "@/lib/etudes";
import { wilsonCi, type TierCell, type TierExportMeta } from "@/lib/stats";

/** Agrège tierlist.json (cellules champion × bucket × région) par champion. */
function aggregateForChart(rows: TierCell[], minGames: number, top: number): WinrateDatum[] {
  const byChamp = new Map<string, { games: number; wins: number }>();
  for (const r of rows) {
    const acc = byChamp.get(r.champion_name) ?? { games: 0, wins: 0 };
    acc.games += r.games;
    acc.wins += r.wins;
    byChamp.set(r.champion_name, acc);
  }
  return Array.from(byChamp.entries())
    .filter(([, a]) => a.games >= minGames)
    .sort((a, b) => b[1].games - a[1].games) // les plus joués…
    .slice(0, top)
    .map(([champion, a]) => ({ champion, winrate: a.wins / a.games }))
    .sort((a, b) => b.winrate - a.winrate); // …classés par winrate
}

/**
 * Composants disponibles dans les MDX d'étude, liés à l'étude courante :
 * - <StudyMeta /> lit meta.json de l'étude automatiquement
 * - <TierTable /> : tableau triable/filtrable depuis tierlist.json + meta.json
 *   d'export (data/etudes/[famille]/[patch]/), agrégats et IC recalculés
 * - <WinrateChart top={12} /> : top des champions les plus joués, classés par
 *   winrate (échantillons suffisants uniquement)
 * - <ChampCard /> : carte champion ponctuelle
 */
export function mdxComponents(etude: Etude): MDXComponents {
  const data = <T,>(file: string) => readStudyData<T>(etude.family, etude.patchSlug, file);
  return {
    StudyMeta: () => <StudyMeta meta={etude.meta} />,
    ChampCard,
    Stat,
    KeyFigure,
    // Chapô : premier paragraphe, légèrement plus grand
    Chapo: ({ children }: { children?: React.ReactNode }) => (
      <p className="etude-chapo">{children}</p>
    ),
    TierTable: ({ file = "tierlist.json" }: { file?: string }) => {
      const rows = data<TierCell[]>(file);
      const meta = data<TierExportMeta>("meta.json");
      return <TierTable rows={rows} meta={meta} />;
    },
    // ATTENTION : dans les MDX d'étude, les props se passent en CHAÎNE
    // (top="14"), jamais en expression JSX (top={14}) — ce pipeline MDX
    // n'évalue pas les expressions et la prop arriverait `undefined`, sans
    // la moindre erreur. D'où la conversion explicite ci-dessous.
    WinrateChart: ({
      file = "tierlist.json",
      top,
      subtitle,
    }: {
      file?: string;
      top?: number | string;
      subtitle?: string;
    }) => {
      const rows = data<TierCell[]>(file);
      const meta = data<TierExportMeta>("meta.json");
      const count = Number(top);
      const limit = Number.isFinite(count) && count > 0 ? Math.floor(count) : 12;
      // le titre (dont le nombre de champions) est composé par le composant
      // depuis les données effectivement tracées
      return (
        <WinrateChart
          data={aggregateForChart(rows, meta.min_cell_games, limit)}
          subtitle={subtitle}
        />
      );
    },
  };
}

// réexporté pour les tests éventuels
export { wilsonCi };
