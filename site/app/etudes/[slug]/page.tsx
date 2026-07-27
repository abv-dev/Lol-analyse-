import type { Metadata } from "next";
import { MDXRemote } from "next-mdx-remote/rsc";
import { mdxComponents } from "@/components/mdx";
import { getAllEtudes, getEtude } from "@/lib/etudes";

// SSG strict : toutes les études sont générées au build, rien de dynamique.
export const dynamicParams = false;

export function generateStaticParams() {
  return getAllEtudes().map((etude) => ({ slug: etude.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const { slug } = await params;
  const { meta } = getEtude(slug);
  return { title: meta.title, description: meta.description };
}

export default async function EtudePage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const etude = getEtude(slug);
  return (
    <article className="prose prose-invert prose-zinc max-w-none prose-headings:text-zinc-100 prose-a:text-accent">
      <MDXRemote source={etude.source} components={mdxComponents(etude)} />
    </article>
  );
}
