import Link from "next/link";
import { KOFI_URL, REPO_URL, pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "À propos",
  description:
    "EloLab est un projet communautaire, gratuit et sans publicité, qui publie des études statistiques du jeu classé. Il est financé par les dons sur Ko-fi.",
  path: "/a-propos",
});

export default function AProposPage() {
  return (
    <article className="prose prose-invert prose-zinc max-w-none prose-headings:text-zinc-100 prose-a:text-accent">
      <h1>À propos</h1>
      <p>
        EloLab publie des études statistiques sur le jeu classé de League of Legends :
        winrate, pick et ban par champion, par rang et par région. Les chiffres viennent
        d&apos;un dataset de matchs ranked solo collecté en continu via l&apos;API officielle
        Riot, et chaque étude indique la taille de son échantillon et l&apos;incertitude de
        ses résultats.
      </p>

      <h2>Un projet communautaire</h2>
      <p>
        EloLab est un projet indépendant, mené à titre non commercial. Le site est gratuit,
        ne demande aucun compte et le code du collecteur, du pipeline d&apos;export et du
        site est{" "}
        <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
          public
        </a>
        . Les remarques sur les données ou la méthode sont bienvenues : c&apos;est ce qui
        permet de corriger les biais que la collecte ne voit pas.
      </p>

      <h2>Sans publicité</h2>
      <p>
        Le site n&apos;affiche aucune publicité, n&apos;utilise aucun traceur ni cookie et
        ne revend aucune donnée. Aucune étude n&apos;est sponsorisée.
      </p>

      <h2>Financement</h2>
      <p>
        Le seul coût récurrent est le serveur qui collecte les matchs, environ 8 € par
        mois. Il est couvert par les dons des lecteurs sur Ko-fi :
      </p>
      <p className="not-prose">
        <a
          href={KOFI_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block rounded-md border border-accent px-4 py-2 text-sm font-medium text-accent hover:bg-accent hover:text-zinc-950"
        >
          Soutenir EloLab sur ko-fi.com/elolab
        </a>
      </p>
      <p>Un don ne donne accès à aucun contenu supplémentaire : tout reste public.</p>

      <h2>Pour aller plus loin</h2>
      <ul>
        <li>
          <Link href="/methodologie">Méthodologie</Link> : collecte, seuils de qualité,
          limites connues.
        </li>
        <li>
          <Link href="/mentions-legales">Mentions légales</Link>.
        </li>
      </ul>
    </article>
  );
}
