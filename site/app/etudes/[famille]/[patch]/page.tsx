import type { Metadata } from "next";
import { MDXRemote } from "next-mdx-remote/rsc";
import remarkGfm from "remark-gfm";
import PatchSelector from "@/components/PatchSelector";
import Support from "@/components/Support";
import { mdxComponents } from "@/components/mdx";
import { getAllEtudes, getEtude } from "@/lib/etudes";
import { FEED_ALTERNATE } from "@/lib/site";

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
  const url = `/etudes/${famille}/${patch}`;
  // Titre de l'étude repris dans la carte de partage (Discord, Slack…) ;
  // l'image reste la carte de marque.
  return {
    title: meta.title,
    description: meta.description,
    // FEED_ALTERNATE est réinjecté : Next remplace `alternates` au lieu de le
    // fusionner, un canonical seul ferait disparaître la balise de flux.
    alternates: { canonical: url, ...FEED_ALTERNATE },
    openGraph: {
      type: "article",
      title: `${meta.title} — EloLab`,
      description: meta.description,
      url,
      images: [{ url: "/og.png", width: 1200, height: 630, alt: "EloLab" }],
    },
    twitter: {
      card: "summary_large_image",
      title: `${meta.title} — EloLab`,
      description: meta.description,
      images: ["/og.png"],
    },
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
      <article className="etude prose prose-invert prose-zinc max-w-none prose-headings:text-zinc-100 prose-a:text-accent prose-a:underline-offset-2">
        {/* remark-gfm : sans lui, les tableaux markdown des études sont
            rendus en texte brut avec les séparateurs |---| visibles. */}
        <MDXRemote
          source={etude.source}
          components={mdxComponents(etude)}
          options={{ mdxOptions: { remarkPlugins: [remarkGfm] } }}
        />
      </article>
      {/* après la section Limites de l'article, avant le footer légal */}
      <Support />
    </div>
  );
}
