import Link from "next/link";
import { getVersions, slugToPatch } from "@/lib/etudes";

/** Navigation dans l'archive d'une famille d'études, par patch. */
export default function PatchSelector({
  family,
  current,
}: {
  family: string;
  current: string;
}) {
  const versions = getVersions(family);
  if (versions.length <= 1) return null;
  return (
    <nav className="not-prose mb-6 flex flex-wrap items-center gap-2 text-sm">
      <span className="text-zinc-500">Patch :</span>
      {versions.map((slug) =>
        slug === current ? (
          <span key={slug}
                className="rounded bg-accent/20 px-2.5 py-1 font-medium text-accent">
            {slugToPatch(slug)}
          </span>
        ) : (
          <Link key={slug} href={`/etudes/${family}/${slug}`}
                className="rounded bg-zinc-800 px-2.5 py-1 text-zinc-300 hover:bg-zinc-700 hover:text-zinc-100">
            {slugToPatch(slug)}
          </Link>
        )
      )}
    </nav>
  );
}
