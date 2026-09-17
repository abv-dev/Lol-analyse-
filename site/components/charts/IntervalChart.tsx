/**
 * Winrates avec leur intervalle de confiance à 95 %, une ligne par entrée.
 *
 * Composant serveur, sans JavaScript : chaque valeur vient de study.json
 * (faits calculés et vérifiés), jamais d'un chiffre écrit dans le MDX.
 */
export interface IntervalRow {
  label: string;
  wr: number;
  lo: number;
  hi: number;
  wrLabel: string;
  gamesLabel: string;
}

export default function IntervalChart({
  title,
  subtitle,
  rows,
}: {
  title: string;
  subtitle?: string;
  rows: IntervalRow[];
}) {
  const min = Math.min(0.5, ...rows.map((r) => r.lo));
  const max = Math.max(0.5, ...rows.map((r) => r.hi));
  const pad = (max - min) * 0.08 || 0.01;
  const lo = min - pad;
  const hi = max + pad;
  const x = (v: number) => `${((v - lo) / (hi - lo)) * 100}%`;
  return (
    <figure className="not-prose my-8">
      <figcaption className="mb-3 text-sm font-medium text-zinc-300">
        {title}
        {subtitle && <span className="text-zinc-500"> — {subtitle}</span>}
      </figcaption>
      <div className="space-y-2 rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
        {rows.map((r) => (
          <div
            key={r.label}
            className="grid grid-cols-[minmax(0,7rem)_1fr_auto] items-center gap-3 text-xs sm:grid-cols-[minmax(0,11rem)_1fr_auto]"
          >
            <span className="truncate text-zinc-300" title={r.label}>
              {r.label}
            </span>
            <div className="relative h-4" aria-hidden>
              <div className="absolute inset-y-1/2 left-0 right-0 h-px bg-zinc-800" />
              <div
                className="absolute top-0 h-4 border-l border-dashed border-zinc-500"
                style={{ left: x(0.5) }}
              />
              <div
                className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded bg-accent/40"
                style={{ left: x(r.lo), width: `calc(${x(r.hi)} - ${x(r.lo)})` }}
              />
              <div
                className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent"
                style={{ left: x(r.wr) }}
              />
            </div>
            <span className="whitespace-nowrap tabular-nums text-zinc-400">
              {r.wrLabel}
              <span className="hidden text-zinc-600 sm:inline"> · {r.gamesLabel}</span>
            </span>
          </div>
        ))}
        <p className="pt-1 text-[11px] text-zinc-500">
          Point : winrate. Barre : intervalle de confiance à 95 %. Trait pointillé : 50 %.
        </p>
      </div>
    </figure>
  );
}
