import type { EtudeMeta } from "@/lib/etudes";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("fr-FR", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

const REGION_LABELS: Record<string, string> = {
  europe: "Europe (EUW)",
  asia: "Asie (KR)",
  americas: "Amériques (NA)",
};

export default function StudyMeta({ meta }: { meta: EtudeMeta }) {
  const rows: [string, string][] = [
    ["Patch étudié", meta.patch],
    ["Taille d'échantillon", `${meta.sample_size.toLocaleString("fr-FR")} matchs`],
    ["Régions couvertes", meta.regions.map((r) => REGION_LABELS[r] ?? r).join(", ")],
    ["Données collectées le", formatDate(meta.collected_at)],
  ];
  return (
    <div className="not-prose my-6 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-zinc-800 bg-zinc-800 sm:grid-cols-4">
      {rows.map(([label, value]) => (
        <div key={label} className="bg-zinc-900 p-3">
          <div className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</div>
          <div className="mt-1 text-sm font-medium text-zinc-200">{value}</div>
        </div>
      ))}
    </div>
  );
}
