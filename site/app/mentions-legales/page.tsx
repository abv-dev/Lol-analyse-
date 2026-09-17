import Link from "next/link";
import { KOFI_URL, SITE_URL, pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Mentions légales",
  description:
    "Éditeur, hébergement, propriété intellectuelle, données personnelles et mention légale Riot Games du site EloLab.",
  path: "/mentions-legales",
});

export default function MentionsLegalesPage() {
  return (
    <article className="prose prose-invert prose-zinc max-w-none prose-headings:text-zinc-100 prose-a:text-accent">
      <h1>Mentions légales</h1>

      <h2>Éditeur</h2>
      <p>
        Le site {SITE_URL.replace(/^https?:\/\//, "")} (EloLab) est édité à titre non
        professionnel par un particulier. Conformément à l&apos;article 6 de la loi n°
        2004-575 du 21 juin 2004 pour la confiance dans l&apos;économie numérique,
        l&apos;éditeur a communiqué les éléments permettant son identification à
        l&apos;hébergeur.
      </p>
      <p>
        Contact : via la{" "}
        <a href={KOFI_URL} target="_blank" rel="noopener noreferrer">
          page Ko-fi du projet
        </a>
        .
      </p>

      <h2>Hébergement</h2>
      <p>
        Vercel Inc., 440 N Barranca Avenue #4133, Covina, CA 91723, États-Unis —{" "}
        <a href="https://vercel.com" target="_blank" rel="noopener noreferrer">
          vercel.com
        </a>
        .
      </p>

      <h2>Riot Games</h2>
      <p lang="en">
        EloLab isn&apos;t endorsed by Riot Games and doesn&apos;t reflect the views or
        opinions of Riot Games or anyone officially involved in producing or managing Riot
        Games properties. Riot Games, and all associated properties are trademarks or
        registered trademarks of Riot Games, Inc.
      </p>
      <p>
        EloLab n&apos;est ni approuvé ni soutenu par Riot Games et ne reflète pas les
        opinions de Riot Games ni de quiconque participe officiellement à la production ou
        à la gestion des propriétés de Riot Games. League of Legends et les noms des
        champions sont des marques ou des marques déposées de Riot Games, Inc.
      </p>

      <h2>Données utilisées</h2>
      <p>
        Les statistiques sont calculées à partir de matchs obtenus via l&apos;API
        officielle Riot Games. Le site ne publie que des agrégats (parties, victoires,
        bans par champion, rang et région) : aucun pseudonyme, identifiant de joueur ou
        historique individuel n&apos;y figure. La méthode est décrite sur la page{" "}
        <Link href="/methodologie">Méthodologie</Link>.
      </p>

      <h2>Données personnelles et cookies</h2>
      <p>
        Le site ne dépose aucun cookie, n&apos;utilise aucun outil de mesure
        d&apos;audience ni aucun script tiers, et ne propose ni compte ni formulaire.
        L&apos;hébergeur peut conserver des journaux techniques de connexion (adresse IP,
        date, page demandée) pour assurer le fonctionnement et la sécurité du service.
      </p>
      <p>
        Le lien de soutien renvoie vers Ko-fi, un service tiers : les données saisies
        lors d&apos;un don relèvent de sa propre politique de confidentialité.
      </p>

      <h2>Propriété intellectuelle</h2>
      <p>
        Les textes, graphiques et analyses d&apos;EloLab peuvent être cités et repris à
        condition de mentionner la source avec un lien vers la page concernée. Les
        éléments appartenant à Riot Games restent soumis aux conditions de Riot Games.
      </p>
    </article>
  );
}
