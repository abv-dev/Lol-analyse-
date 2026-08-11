import type { Metadata } from "next";
import { MDXRemote } from "next-mdx-remote/rsc";
import remarkGfm from "remark-gfm";
import PatchSelector from "@/components/PatchSelector";
import { mdxComponents } from "@/components/mdx";
import { getAllEtudes, getEtude } from "@/lib/etudes";

// SSG strict : toutes les versions d'études sont générées au build.
export const dynamicParams = false;

export function generateStaticParams() {
  return getAllEtudes().map((etude) => ({
    famille: etude.family,
    patch: etude.patchSlug,
  }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<{ famille: string; patch: string }>;
}): Promise<Metadata> {
  const { famille, patch } = await params;
  const { meta } = getEtude(famille, patch);
  return {
    title: meta.title,
    description: meta.description,
    alternates: { canonical: `/etudes/${famille}/${patch}` },
  };
}

export default async function EtudePage({
  params,
}: {
  params: Promise<{ famille: string; patch: string }>;
}) {
  const { famille, patch } = await params;
  const etude = getEtude(famille, patch);
  return (
    <div>
      <PatchSelector family={famille} current={patch} />
      <article className="prose prose-invert prose-zinc max-w-none prose-headings:text-zinc-100 prose-a:text-accent">
        {/* remark-gfm : sans lui, les tableaux markdown des études sont
            rendus en texte brut avec les séparateurs |---| visibles. */}
        <MDXRemote
          source={etude.source}
          components={mdxComponents(etude)}
          options={{ mdxOptions: { remarkPlugins: [remarkGfm] } }}
        />
      </article>
    </div>
  );
}
