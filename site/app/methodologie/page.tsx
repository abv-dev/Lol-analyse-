import Support from "@/components/Support";
import { getEtude, getFamilies, getLatestPatchSlug, readStudyData } from "@/lib/etudes";
import { REPO_URL, pageMetadata } from "@/lib/site";

export const metadata = pageMetadata({
  title: "Méthodologie",
  description:
    "Comment les données EloLab sont produites : gate qualité de 20 000 matchs minimum par région, échantillonnage par rang, intervalles de confiance et pipeline reproductible.",
  path: "/methodologie",
});

/** Seuil appliqué par l'export (lolcollector/export.py, MIN_REGION_MATCHES). */
const MIN_REGION_MATCHES = 20000;

const REGION_LABELS: Record<string, string> = {
  europe: "Europe (EUW)",
  asia: "Asie (KR)",
  americas: "Amériques (NA)",
};

type ExportMeta = {
  patch: string;
  min_cell_games: number;
  cells: { region: string; bucket: string; matches: number }[];
};

/** Volumes par région de la dernière tier list publiée, lus dans son export. */
function latestRegionVolumes() {
  if (!getFamilies().includes("tierlist")) return null;
  const slug = getLatestPatchSlug("tierlist");
  const data = readStudyData<ExportMeta>("tierlist", slug, "meta.json");
  const totals = new Map<string, number>();
  for (const cell of data.cells) {
    totals.set(cell.region, (totals.get(cell.region) ?? 0) + cell.matches);
  }
  return {
    patch: getEtude("tierlist", slug).meta.patch,
    minCellGames: data.min_cell_games,
    regions: [...totals.entries()].sort((a, b) => b[1] - a[1]),
  };
}

const fmt = (n: number) => n.toLocaleString("fr-FR");

export default function MethodologiePage() {
  const volumes = latestRegionVolumes();
  return (
    <>
      <article className="prose prose-invert prose-zinc max-w-none prose-headings:text-zinc-100 prose-a:text-accent">
        <h1>Méthodologie</h1>
        <p>
          Les études publiées ici s&apos;appuient sur un dataset de matchs{" "}
          <strong>ranked solo/duo (file 420)</strong> collecté en continu via l&apos;API
          officielle Riot, sur trois régions : Europe (EUW), Asie (KR) et Amériques (NA).
          Cette page décrit comment ces matchs sont choisis, à partir de quand ils sont
          jugés suffisants pour être publiés, et comment chaque chiffre peut être
          recalculé.
        </p>

        <h2>Gate qualité : 20 000 matchs minimum par région</h2>
        <p>
          Aucune étude n&apos;est exportée tant qu&apos;une des régions couvertes compte{" "}
          <strong>moins de {fmt(MIN_REGION_MATCHES)} matchs</strong> sur le patch étudié.
          Le contrôle est appliqué par la commande d&apos;export elle-même : en dessous du
          seuil, elle refuse d&apos;écrire les fichiers de données et le site ne peut pas
          être mis à jour. Sous ce volume, le découpage par région et par rang ne laisse
          presque aucune cellule exploitable, et une comparaison entre régions
          n&apos;aurait pas de sens.
        </p>
        <p>Deux seuils plus fins s&apos;ajoutent à ce gate :</p>
        <ul>
          <li>
            <strong>
              {fmt(volumes?.minCellGames ?? 200)} parties minimum par cellule
            </strong>{" "}
            (champion × rang × région). Une cellule sous ce seuil reste visible dans les
            tableaux, grisée, mais ne sert jamais de base à une affirmation dans le texte.
          </li>
          <li>
            <strong>Aucune cellule exploitable, aucun export</strong> : un patch dont la
            collecte vient de commencer ne peut pas remplacer une étude publiée.
          </li>
        </ul>
        {volumes && (
          <>
            <p>
              Volumes de la tier list du patch {volumes.patch}, lus dans son fichier
              d&apos;export :
            </p>
            <table>
              <thead>
                <tr>
                  <th>Région</th>
                  <th className="text-right">Matchs</th>
                </tr>
              </thead>
              <tbody>
                {volumes.regions.map(([region, matches]) => (
                  <tr key={region}>
                    <td>{REGION_LABELS[region] ?? region}</td>
                    <td className="text-right tabular-nums">{fmt(matches)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        <h2>Échantillonnage par rang</h2>
        <p>
          Les joueurs sont échantillonnés via l&apos;endpoint League-Exp-V4 en parcourant
          le ladder par <strong>buckets de rang</strong> : Fer+Bronze, Argent+Or,
          Platine+Émeraude, Diamant et au-delà. Le parcours alterne entre les buckets
          (round-robin), dans chaque région, pour que chaque niveau de jeu soit représenté
          par un volume comparable plutôt que proportionnellement à la population du
          ladder. La position de parcours est conservée entre deux redémarrages du
          collecteur.
        </p>
        <p>
          Pour chaque joueur échantillonné, on récupère ses matchs classés des 28 derniers
          jours. Un match n&apos;est stocké qu&apos;une seule fois, même s&apos;il est
          atteint via plusieurs joueurs.
        </p>

        <h3>Approximation du rang d&apos;une partie</h3>
        <p>
          L&apos;API Riot n&apos;expose pas le rang des dix joueurs d&apos;un match. Le
          niveau d&apos;une partie est donc <strong>approximé</strong> par le bucket du
          joueur échantillonné qui a mené à ce match. Une partie classée « Argent+Or » peut
          contenir des joueurs de rangs voisins : les découpages par rang sont des
          tendances, pas des frontières exactes.
        </p>

        <h2>Incertitude</h2>
        <p>
          Chaque winrate est accompagné de son <strong>intervalle de confiance à 95 %</strong>{" "}
          (méthode de Wilson) et du nombre de parties sur lequel il est calculé. Quand deux
          intervalles se recouvrent, les champions sont décrits comme indistinguables, pas
          classés. Les agrégats (plusieurs rangs ou régions à la fois) sont recalculés en
          additionnant parties et victoires, jamais en moyennant des pourcentages.
        </p>

        <h2>Reproductibilité du pipeline</h2>
        <p>
          La chaîne qui va du match collecté au chiffre publié est entièrement scriptée, et
          son code est public :{" "}
          <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
            dépôt du projet
          </a>
          .
        </p>
        <ol>
          <li>
            <strong>Collecte</strong> : le collecteur interroge l&apos;API Riot et stocke
            les matchs dans une base SQLite, avec le patch, la région et le bucket de rang
            de chacun.
          </li>
          <li>
            <strong>Export</strong> : une commande unique produit les fichiers de données
            d&apos;une étude (parties, victoires, bans et intervalles par champion, rang et
            région) ainsi qu&apos;un fichier de métadonnées qui consigne le patch, la
            version de Data Dragon, la période de collecte, le volume de chaque cellule et
            les seuils appliqués. Les gates qualité décrits plus haut sont vérifiés à cette
            étape.
          </li>
          <li>
            <strong>Vérification</strong> : avant publication, un script extrait chaque
            nombre cité dans le texte de l&apos;étude et le compare aux données exportées
            du champion concerné. Un seul chiffre introuvable bloque la publication.
          </li>
          <li>
            <strong>Publication</strong> : le site est généré statiquement à partir de ces
            fichiers. Le build échoue si les métadonnées d&apos;une étude sont incomplètes.
            Les tableaux interactifs recalculent winrates et intervalles côté navigateur à
            partir des compteurs bruts, avec les mêmes formules que l&apos;export.
          </li>
        </ol>
        <p>
          Chaque étude reste accessible à l&apos;URL de son patch : une version publiée
          n&apos;est jamais réécrite par la suivante.
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
            <strong>Fenêtre de collecte</strong> : seuls les matchs de moins de 28 jours
            sont collectés, les joueurs inactifs sont donc exclus.
          </li>
          <li>
            Les winrates de champions à faible pickrate ont des intervalles de confiance
            larges : chaque étude indique la taille de son échantillon.
          </li>
        </ul>

        <h2>Fraîcheur des données</h2>
        <p>
          Chaque étude affiche le <strong>patch étudié</strong> et la{" "}
          <strong>date de collecte</strong>. Les études sensibles au patch (tier lists,
          statistiques d&apos;équilibrage) ont vocation à être rafraîchies à chaque nouveau
          patch, dès que le gate qualité est de nouveau franchi.
        </p>
      </article>
      <Support />
    </>
  );
}
