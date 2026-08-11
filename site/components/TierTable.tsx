"use client";

import { useMemo, useState } from "react";
import {
  BUCKET_LABELS,
  REGION_LABELS,
  pct,
  wilsonCi,
  type TierCell,
  type TierExportMeta,
} from "@/lib/stats";

type SortKey = "winrate" | "pick_rate" | "ban_rate" | "games";

/** Minuscules sans accents ni apostrophes : « seraphine » trouve « Séraphine »,
 *  « kaisa » trouve « Kai'Sa ». */
function normalise(value: string): string {
  return value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/['''`.\s-]/g, "")
    .toLowerCase();
}

interface AggRow {
  champion: string;
  games: number;
  wins: number;
  bans: number;
  winrate: number;
  ciLow: number;
  ciHigh: number;
  pickRate: number;
  banRate: number;
  insufficient: boolean;
}

/**
 * Tableau triable (winrate / pick rate / ban rate / games) et filtrable par
 * bucket de rank et région. Les agrégats sur « tous » recombinent games/wins/
 * bans des cellules sélectionnées et recalculent winrate + IC de Wilson —
 * jamais de moyenne de pourcentages.
 */
export default function TierTable({
  rows,
  meta,
}: {
  rows: TierCell[];
  meta: TierExportMeta;
}) {
  const [bucket, setBucket] = useState<string>("ALL");
  const [region, setRegion] = useState<string>("ALL");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("winrate");
  const [descending, setDescending] = useState(true);

  const buckets = useMemo(
    () => Array.from(new Set(rows.map((r) => r.bucket))).sort(), [rows]);
  const regions = useMemo(
    () => Array.from(new Set(rows.map((r) => r.region))).sort(), [rows]);

  const { aggRows, denomMatches } = useMemo(() => {
    const selected = rows.filter(
      (r) => (bucket === "ALL" || r.bucket === bucket) &&
             (region === "ALL" || r.region === region)
    );
    const denom = meta.cells
      .filter((c) => (bucket === "ALL" || c.bucket === bucket) &&
                     (region === "ALL" || c.region === region))
      .reduce((sum, c) => sum + c.matches, 0);
    const byChamp = new Map<string, { games: number; wins: number; bans: number }>();
    for (const r of selected) {
      const acc = byChamp.get(r.champion_name) ?? { games: 0, wins: 0, bans: 0 };
      acc.games += r.games;
      acc.wins += r.wins;
      acc.bans += r.bans;
      byChamp.set(r.champion_name, acc);
    }
    const result: AggRow[] = Array.from(byChamp.entries()).map(([champion, a]) => {
      const [ciLow, ciHigh] = wilsonCi(a.wins, a.games);
      return {
        champion,
        games: a.games,
        wins: a.wins,
        bans: a.bans,
        winrate: a.games ? a.wins / a.games : 0,
        ciLow,
        ciHigh,
        pickRate: denom ? a.games / denom : 0,
        banRate: denom ? a.bans / denom : 0,
        insufficient: a.games < meta.min_cell_games,
      };
    });
    return { aggRows: result, denomMatches: denom };
  }, [rows, meta, bucket, region]);

  const sorted = useMemo(() => {
    const key: Record<SortKey, (r: AggRow) => number> = {
      winrate: (r) => r.winrate,
      pick_rate: (r) => r.pickRate,
      ban_rate: (r) => r.banRate,
      games: (r) => r.games,
    };
    const needle = normalise(query);
    const filtered = needle
      ? aggRows.filter((r) => normalise(r.champion).includes(needle))
      : aggRows;
    // Les cellules sous le seuil vont en bas quel que soit le tri
    return [...filtered].sort((a, b) => {
      if (a.insufficient !== b.insufficient) return a.insufficient ? 1 : -1;
      return (key[sortKey](b) - key[sortKey](a)) * (descending ? 1 : -1);
    });
  }, [aggRows, sortKey, descending, query]);

  const header = (label: string, keyName: SortKey) => (
    <th className="px-3 py-2 text-right">
      <button
        onClick={() => {
          if (sortKey === keyName) setDescending(!descending);
          else { setSortKey(keyName); setDescending(true); }
        }}
        className={`hover:text-zinc-100 ${sortKey === keyName ? "text-accent" : ""}`}
      >
        {label} {sortKey === keyName ? (descending ? "↓" : "↑") : ""}
      </button>
    </th>
  );

  const selectClass =
    "rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-200";

  return (
    <div className="not-prose my-8">
      <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2 text-zinc-400">
          <span className="sr-only sm:not-sr-only">Champion</span>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher un champion…"
            aria-label="Rechercher un champion"
            className="w-52 rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-sm text-zinc-200 placeholder:text-zinc-600"
          />
        </label>
        <label className="flex items-center gap-2 text-zinc-400">
          Rank
          <select value={bucket} onChange={(e) => setBucket(e.target.value)}
                  className={selectClass}>
            <option value="ALL">Tous les buckets</option>
            {buckets.map((b) => (
              <option key={b} value={b}>{BUCKET_LABELS[b] ?? b}</option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-zinc-400">
          Région
          <select value={region} onChange={(e) => setRegion(e.target.value)}
                  className={selectClass}>
            <option value="ALL">Toutes les régions</option>
            {regions.map((r) => (
              <option key={r} value={r}>{REGION_LABELS[r] ?? r}</option>
            ))}
          </select>
        </label>
        <span className="text-xs text-zinc-500">
          {denomMatches.toLocaleString("fr-FR")} matchs dans la sélection
          {query && ` · ${sorted.length} champion${sorted.length > 1 ? "s" : ""} trouvé${sorted.length > 1 ? "s" : ""}`}
        </span>
      </div>
      <div className="overflow-x-auto rounded-lg border border-zinc-800">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="bg-zinc-900 text-xs uppercase tracking-wide text-zinc-500">
            <tr>
              <th className="px-3 py-2 text-left">Champion</th>
              {header("Games", "games")}
              {header("Winrate", "winrate")}
              <th className="px-3 py-2 text-right">IC 95 %</th>
              {header("Pick", "pick_rate")}
              {header("Ban", "ban_rate")}
            </tr>
          </thead>
          <tbody className="divide-y divide-zinc-800/70">
            {sorted.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-zinc-500">
                  Aucun champion ne correspond à « {query} ».
                </td>
              </tr>
            )}
            {sorted.map((r) => (
              <tr key={r.champion}
                  className={r.insufficient ? "opacity-50" : "hover:bg-zinc-900/50"}>
                <td className="px-3 py-2 font-medium text-zinc-200">
                  {r.champion}
                  {r.insufficient && (
                    <span className="ml-2 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">
                      &lt; {meta.min_cell_games} games
                    </span>
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-400">
                  {r.games.toLocaleString("fr-FR")}
                </td>
                <td className={`px-3 py-2 text-right font-semibold tabular-nums ${
                  r.winrate >= 0.52 ? "text-emerald-400"
                    : r.winrate <= 0.48 ? "text-red-400" : "text-zinc-200"}`}>
                  {pct(r.winrate)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-500">
                  [{pct(r.ciLow)} – {pct(r.ciHigh)}]
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                  {pct(r.pickRate)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                  {pct(r.banRate)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-zinc-500">
        Winrate avec intervalle de confiance de Wilson à 95 %. Les lignes sous{" "}
        {meta.min_cell_games} games dans la sélection sont grisées : échantillon
        insuffisant pour conclure. Pick et ban rates rapportés aux{" "}
        {denomMatches.toLocaleString("fr-FR")} matchs de la sélection.
      </p>
    </div>
  );
}
