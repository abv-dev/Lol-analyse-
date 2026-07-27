import StudyCard from "@/components/StudyCard";
import { getAllEtudes } from "@/lib/etudes";

export default function Home() {
  const etudes = getAllEtudes(); // les plus récentes en premier
  return (
    <div>
      <h1 className="text-3xl font-bold text-zinc-100">
        Le laboratoire de données de la Faille
      </h1>
      <p className="mt-3 max-w-2xl text-sm text-zinc-400">
        EloLab mesure le jeu classé de League of Legends : pick, ban et winrate par rank
        et région, à partir d&apos;un dataset de matchs ranked solo collecté en continu
        via l&apos;API officielle Riot. Des données, pas des impressions.
      </p>
      <h2 className="mt-10 text-xl font-semibold text-zinc-100">Études</h2>
      <div className="mt-8 space-y-4">
        {etudes.length === 0 ? (
          <p className="text-zinc-500">Aucune étude publiée pour l&apos;instant.</p>
        ) : (
          etudes.map((etude) => <StudyCard key={etude.slug} etude={etude} />)
        )}
      </div>
    </div>
  );
}
