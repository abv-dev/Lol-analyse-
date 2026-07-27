interface ChampCardProps {
  name: string;
  role?: string;
  winrate: number; // 0..1
  pickrate?: number; // 0..1
  banrate?: number; // 0..1
  note?: string;
}

function pct(value: number) {
  return `${(value * 100).toFixed(1).replace(".", ",")} %`;
}

/** Carte champion : avatar par initiales (pas d'assets Riot embarqués). */
export default function ChampCard({ name, role, winrate, pickrate, banrate, note }: ChampCardProps) {
  const initials = name
    .split(/[\s']/)
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  const winrateColor =
    winrate >= 0.52 ? "text-emerald-400" : winrate <= 0.48 ? "text-red-400" : "text-zinc-200";
  return (
    <div className="not-prose my-4 flex items-center gap-4 rounded-lg border border-zinc-800 bg-zinc-900/60 p-4">
      <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent/15 text-sm font-bold text-accent">
        {initials}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="font-semibold text-zinc-100">{name}</span>
          {role && <span className="text-xs text-zinc-500">{role}</span>}
        </div>
        {note && <p className="mt-1 text-sm text-zinc-400">{note}</p>}
      </div>
      <div className="flex shrink-0 gap-5 text-right text-sm">
        <div>
          <div className={`font-semibold ${winrateColor}`}>{pct(winrate)}</div>
          <div className="text-[11px] text-zinc-500">winrate</div>
        </div>
        {pickrate !== undefined && (
          <div>
            <div className="font-semibold text-zinc-200">{pct(pickrate)}</div>
            <div className="text-[11px] text-zinc-500">pickrate</div>
          </div>
        )}
        {banrate !== undefined && (
          <div>
            <div className="font-semibold text-zinc-200">{pct(banrate)}</div>
            <div className="text-[11px] text-zinc-500">banrate</div>
          </div>
        )}
      </div>
    </div>
  );
}
