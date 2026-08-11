import Link from "next/link";
import type { Etude } from "@/lib/etudes";

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString("fr-FR", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });
}

export default function StudyCard({ etude }: { etude: Etude }) {
  const { family, patchSlug, meta } = etude;
  return (
    <Link
      href={`/etudes/${family}/${patchSlug}`}
      className="block rounded-lg border border-zinc-800 bg-zinc-900/60 p-5 transition hover:border-accent-dim hover:bg-zinc-900"
    >
      <div className="flex items-baseline justify-between gap-4">
        <h2 className="text-base font-semibold text-zinc-100">{meta.title}</h2>
        <span className="shrink-0 rounded bg-accent/15 px-2 py-0.5 text-xs font-medium text-accent">
          Patch {meta.patch}
        </span>
      </div>
      <p className="mt-2 text-sm text-zinc-400">{meta.description}</p>
      <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-zinc-500">
        <span>{formatDate(meta.date)}</span>
        <span aria-hidden>·</span>
        {meta.tags.map((tag) => (
          <span key={tag} className="rounded bg-zinc-800 px-2 py-0.5 text-zinc-400">
            {tag}
          </span>
        ))}
      </div>
    </Link>
  );
}
