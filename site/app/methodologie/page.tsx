import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Méthodologie",
  description:
    "Comment les données sont collectées : sampling par bucket de rank, approximation du tier, limites connues.",
};

export default function MethodologiePage() {
  return (
    <article className="prose prose-invert prose-zinc max-w-none prose-headings:text-zinc-100 prose-a:text-accent">
      <h1>Méthodologie</h1>
      <p>
        Les études publiées ici s&apos;appuient sur un dataset de matchs{" "}
        <strong>ranked solo/duo (file 420)</strong> collecté en continu via l&apos;API
        officielle Riot, sur trois régions : Europe (EUW), Asie (KR) et Amériques (NA).
      </p>

      <h2>Échantillonnage par bucket de rank</h2>
      <p>
        Les joueurs sont échantillonnés via League-Exp-V4 en parcourant le ladder par{" "}
        <strong>buckets de tiers</strong> : Fer+Bronze, Argent+Or, Platine+Émeraude,
        Diamant et au-delà. Le parcours alterne entre les buckets (round-robin) pour
        garder un dataset équilibré entre les niveaux de jeu. Pour chaque joueur
        échantillonné, on récupère ses matchs ranked récents ; chaque match n&apos;est
        stocké qu&apos;une seule fois.
      </p>

      <h2>Approximation du tier d&apos;une partie</h2>
      <p>
        L&apos;API Riot n&apos;expose pas le rank des dix joueurs d&apos;un match. Le
        niveau d&apos;une partie est donc <strong>approximé</strong> par le bucket du
        joueur échantillonné qui a mené à ce match. Une partie classée « Argent+Or » peut
        contenir des joueurs de tiers voisins : les découpages par rank sont des
        tendances, pas des frontières exactes.
      </p>

      <h2>Limites connues</h2>
      <ul>
        <li>
          <strong>Biais d&apos;activité</strong> : les joueurs qui jouent beaucoup sont
          surreprésentés dans l&apos;échantillon.
        </li>
        <li>
          <strong>Une plateforme par région</strong> : EUW, KR et NA servent de proxy
          pour leurs routings respectifs ; les autres serveurs (EUNE, BR, etc.) ne sont
          pas couverts.
        </li>
        <li>
          <strong>Parcours de ladder non uniforme</strong> : l&apos;échantillonnage par
          pages de ladder n&apos;est pas un tirage aléatoire parfait.
        </li>
        <li>
          <strong>Attribution du bucket</strong> : un match découvert via plusieurs
          joueurs est attribué au bucket du premier joueur qui l&apos;a fait découvrir.
        </li>
        <li>
          Les winrates de champions à faible pickrate ont des intervalles de confiance
          larges : chaque étude indique la taille de son échantillon.
        </li>
      </ul>

      <h2>Fraîcheur des données</h2>
      <p>
        Chaque étude affiche le <strong>patch étudié</strong> et la{" "}
        <strong>date de collecte</strong>. Les études sensibles au patch (tierlists,
        statistiques d&apos;équilibrage) sont marquées comme telles et ont vocation à
        être rafraîchies à chaque nouveau patch.
      </p>
    </article>
  );
}
