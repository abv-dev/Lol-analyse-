"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

export interface WinrateDatum {
  champion: string;
  winrate: number; // 0..1
  pickrate?: number; // 0..1
}

/**
 * Barres de winrate par champion, ligne de référence à 50 %.
 *
 * Le nombre de champions du titre est composé à partir des données réelles
 * (`data.length`) et jamais écrit à la main : titre et graphique ne peuvent
 * plus diverger. `subtitle` porte le contexte (« toutes régions, tous ranks »).
 */
export default function WinrateChart({
  data,
  subtitle,
}: {
  data: WinrateDatum[];
  subtitle?: string;
}) {
  const rows = data.map((d) => ({ ...d, winratePct: +(d.winrate * 100).toFixed(1) }));
  const caption = `Winrate des ${rows.length} champions les plus joués`
    + (subtitle ? ` — ${subtitle}` : "");
  return (
    <figure className="not-prose my-8">
      <figcaption className="mb-3 text-sm font-medium text-zinc-300">{caption}</figcaption>
      <div className="h-80 w-full rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, left: -16, bottom: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#27272a" vertical={false} />
            <XAxis
              dataKey="champion"
              tick={{ fill: "#a1a1aa", fontSize: 12 }}
              tickLine={false}
              axisLine={{ stroke: "#3f3f46" }}
              interval={0}
              angle={-30}
              textAnchor="end"
              height={60}
            />
            <YAxis
              domain={[40, 60]}
              unit=" %"
              tick={{ fill: "#a1a1aa", fontSize: 12 }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip
              formatter={(value: number) => [`${value} %`, "Winrate"]}
              contentStyle={{
                backgroundColor: "#18181b",
                border: "1px solid #3f3f46",
                borderRadius: 8,
                color: "#e4e4e7",
              }}
              cursor={{ fill: "#27272a", opacity: 0.4 }}
            />
            <ReferenceLine y={50} stroke="#71717a" strokeDasharray="4 4" />
            <Bar dataKey="winratePct" fill="#c8aa6e" radius={[3, 3, 0, 0]} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}
