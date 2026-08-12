"use client";

import { useMemo, useState } from "react";
import { STAT_VARIANT } from "@/lib/statVariant";
import {
  BUCKET_LABELS,
  REGION_LABELS,
  ROLE_LABELS,
  pct,
  wilsonCi,
  type RoleCell,
  type TierCell,
  type TierExportMeta,
} from "@/lib/stats";

type SortKey = "winrate" | "pick_rate" | "ban_rate" | "games";

/** Intervalle de confiance rendu selon la variante choisie pour tout le site
 *  (voir components/Stat.tsx). */
function ciLabel(low: number, high: number): string {
  const l = (low * 100).toFixed(1).replace(".", ",");
  const h = (high * 100).toFixed(1).replace(".", ",");
  if (STAT_VARIANT === "plage") return `${l}–${h}`;
  if (STAT_VARIANT === "exposant")
    return `±${(((high - low) / 2) * 100).toFixed(2).replace(".", ",")}`;
  return `[${l} – ${h}]`;
}

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
  winrate: number;
  ciLow: number;
  ciHigh: number;
  pickRate: number;
  /** null sur une sélection de poste : un ban n'a pas de poste. */
  banRate: number | null;
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
  roleRows,
}: {
  rows: TierCell[];
  meta: TierExportMeta;
  roleRows?: RoleCell[] | null;
}) {
  const [bucket, setBucket] = useState<string>("ALL");
  const [region, setRegion] = useState<string>("ALL");
  const [role, setRole] = useState<string>("ALL");
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("winrate");
  const [descending, setDescending] = useState(true);

  const buckets = useMemo(
    () => Array.from(new Set(rows.map((r) => r.bucket))).sort(), [rows]);
  const regions = useMemo(
    () => Array.from(new Set(rows.map((r) => r.region))).sort(), [rows]);
  // Ordre de la Faille plutôt qu'alphabétique
  const roles = useMemo(() => {
    if (!roleRows?.length) return [];
    const present = new Set(roleRows.map((r) => r.role));
    return Object.keys(ROLE_LABELS).filter((r) => present.has(r));
  }, [roleRows]);

  // tierlist-roles.json ne porte que des champion_id : le nom vient du
  // fichier principal, qui liste tous les champions du dataset.
  const championNames = useMemo(() => {
    const map = new Map<number, string>();
    for (const r of rows) map.set(r.champion_id, r.champion_name);
    return map;
  }, [rows]);

  const byRole = role !== "ALL";

  const { aggRows, denomMatches } = useMemo(() => {
    const denom = meta.cells
      .filter((c) => (bucket === "ALL" || c.bucket === bucket) &&
                     (region === "ALL" || c.region === region))
      .reduce((sum, c) => sum + c.matches, 0);

    // games/wins/bans recombinés depuis les cellules, puis winrate et IC
    // recalculés dessus — jamais une moyenne de pourcentages.
    const byChamp = new Map<string, { games: number; wins: number; bans: number | null }>();
    const add = (champion: string, games: number, wins: number, bans: number | null) => {
      const acc = byChamp.get(champion) ?? { games: 0, wins: 0, bans };
      acc.games += games;
      acc.wins += wins;
      if (acc.bans !== null && bans !== null) acc.bans += bans;
      byChamp.set(champion, acc);
    };

    if (byRole && roleRows) {
      for (const r of roleRows) {
        if (r.role !== role) continue;
        if (bucket !== "ALL" && r.bucket !== bucket) continue;
        if (region !== "ALL" && r.region !== region) continue;
        add(championNames.get(r.champion_id) ?? String(r.champion_id),
            r.games, r.wins, null);
      }
    } else {
      for (const r of rows) {
        if (bucket !== "ALL" && r.bucket !== bucket) continue;
        if (region !== "ALL" && r.region !== region) continue;
        add(r.champion_name, r.games, r.wins, r.bans);
      }
    }

    const result: AggRow[] = Array.from(byChamp.entries()).map(([champion, a]) => {
      const [ciLow, ciHigh] = wilsonCi(a.wins, a.games);
      return {
        champion,
        games: a.games,
        wins: a.wins,
        winrate: a.games ? a.wins / a.games : 0,
        ciLow,
        ciHigh,
        pickRate: denom ? a.games / denom : 0,
        banRate: denom && a.bans !== null ? a.bans / denom : null,
        insufficient: a.games < meta.min_cell_games,
      };
    });
    return { aggRows: result, denomMatches: denom };
  }, [rows, roleRows, championNames, meta, bucket, region, role, byRole]);

  const sorted = useMemo(() => {
    const key: Record<SortKey, (r: AggRow) => number> = {
      winrate: (r) => r.winrate,
      pick_rate: (r) => r.pickRate,
      ban_rate: (r) => r.banRate ?? -1,
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
        {/* Absent des études exportées avant la dimension rôle. */}
        {roles.length > 0 && (
          <label className="flex items-center gap-2 text-zinc-400">
            Poste
            <select
              value={role}
              onChange={(e) => {
                setRole(e.target.value);
                // Le tri par ban n'a plus de sens sur une sélection de poste
                if (e.target.value !== "ALL" && sortKey === "ban_rate") {
                  setSortKey("winrate");
                  setDescending(true);
                }
              }}
              className={selectClass}
            >
              <option value="ALL">Tous les postes</option>
              {roles.map((r) => (
                <option key={r} value={r}>{ROLE_LABELS[r] ?? r}</option>
              ))}
            </select>
          </label>
        )}
        <span className="text-xs text-zinc-500">
          {denomMatches.toLocaleString("fr-FR")} matchs dans la sélection
          {query && ` · ${sorted.length} champion${sorted.length > 1 ? "s" : ""} trouvé${sorted.length > 1 ? "s" : ""}`}
        </span>
      </div>
      {/* 173 lignes : l'en-tête reste collé en haut du conteneur pendant le
          défilement, sinon on perd le sens des colonnes au bout de 20 lignes. */}
      <div className="max-h-[70vh] overflow-auto rounded-lg border border-zinc-800">
        <table className="w-full min-w-[640px] border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-zinc-900 text-xs uppercase tracking-wide text-zinc-500 shadow-[0_1px_0_0_theme(colors.zinc.700)]">
            <tr>
              {/* colonne collée à gauche : sur mobile le tableau défile
                  horizontalement, le nom doit rester lisible */}
              <th className="sticky left-0 z-20 bg-zinc-900 px-3 py-2 text-left">
                Champion
              </th>
              {header("Games", "games")}
              {header("Winrate", "winrate")}
              <th className="px-3 py-2 text-right">IC 95 %</th>
              {header("Pick", "pick_rate")}
              {header("Ban", "ban_rate")}
            </tr>
          </thead>
          {/* Fonds OPAQUES (et non zinc-900/40) : la première colonne est
              collée à gauche et hérite du fond de sa ligne — un fond
              semi-transparent s'y appliquerait une seconde fois et se
              verrait. */}
          <tbody>
            {sorted.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-6 text-center text-zinc-500">
                  Aucun champion ne correspond à « {query} ».
                </td>
              </tr>
            )}
            {sorted.map((r, i) => (
              <tr key={r.champion}
                  className={`${i % 2 ? "bg-[#0f0f12]" : "bg-zinc-950"} ${
                    r.insufficient ? "opacity-50" : "hover:brightness-125"}`}>
                <td className="sticky left-0 bg-inherit px-3 py-2 font-medium text-zinc-200">
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
                {/* colonne secondaire : plus petite et plus atténuée que le
                    winrate qu'elle qualifie */}
                <td className="px-3 py-2 text-right text-xs tabular-nums text-zinc-500">
                  {ciLabel(r.ciLow, r.ciHigh)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                  {pct(r.pickRate)}
                </td>
                <td className="px-3 py-2 text-right tabular-nums text-zinc-300">
                  {r.banRate === null
                    ? <span className="text-zinc-600" title="Un ban vise un champion pour toute la partie : il n'a pas de poste.">—</span>
                    : pct(r.banRate)}
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
