/** Intervalle de confiance de Wilson à 95 % — même formule que l'exporteur. */
const Z95 = 1.959963984540054;

export function wilsonCi(wins: number, games: number): [number, number] {
  if (games === 0) return [0, 0];
  const phat = wins / games;
  const z2 = Z95 * Z95;
  const denom = 1 + z2 / games;
  const centre = phat + z2 / (2 * games);
  const margin = Z95 * Math.sqrt((phat * (1 - phat) + z2 / (4 * games)) / games);
  return [(centre - margin) / denom, (centre + margin) / denom];
}

export function pct(value: number, digits = 1): string {
  return `${(value * 100).toFixed(digits).replace(".", ",")} %`;
}

export const BUCKET_LABELS: Record<string, string> = {
  IRON_BRONZE: "Fer–Bronze",
  SILVER_GOLD: "Argent–Or",
  PLAT_EMERALD: "Platine–Émeraude",
  DIAMOND_PLUS: "Diamant+",
};

export const REGION_LABELS: Record<string, string> = {
  europe: "Europe (EUW)",
  asia: "Asie (KR)",
  americas: "Amériques (NA)",
};

/** Une ligne de tierlist.json produite par `collector.py export`. */
export interface TierCell {
  champion_id: number;
  champion_name: string;
  region: string;
  bucket: string;
  games: number;
  wins: number;
  winrate: number | null;
  winrate_ci_low: number | null;
  winrate_ci_high: number | null;
  pick_rate: number | null;
  ban_rate: number | null;
  bans: number;
  insufficient_sample: boolean;
}

export interface TierExportMeta {
  study: string;
  patch: string;
  exported_at: string;
  collected_from: string | null;
  collected_to: string | null;
  total_matches: number;
  regions: string[];
  min_cell_games: number;
  cells: { region: string; bucket: string; matches: number }[];
}
