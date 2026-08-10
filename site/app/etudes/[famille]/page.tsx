import { redirect } from "next/navigation";
import { getFamilies, getLatestPatchSlug } from "@/lib/etudes";

// URL canonique d'une famille d'études : /etudes/tierlist redirige vers la
// version la plus récente (/etudes/tierlist/16-15). Les URLs datées, elles,
// sont permanentes.
export const dynamicParams = false;

export function generateStaticParams() {
  return getFamilies().map((famille) => ({ famille }));
}

export default async function FamilleCanonique({
  params,
}: {
  params: Promise<{ famille: string }>;
}) {
  const { famille } = await params;
  redirect(`/etudes/${famille}/${getLatestPatchSlug(famille)}`);
}
