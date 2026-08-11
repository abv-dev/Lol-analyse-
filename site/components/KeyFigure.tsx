import Stat from "@/components/Stat";

/**
 * Chiffre-clé d'une section, sorti du fil du texte.
 *
 * Un filet doré à gauche, le chiffre en grand, une phrase de contexte : le
 * lecteur qui parcourt la page en diagonale doit pouvoir retenir un résultat
 * par section sans lire le paragraphe. Pas d'encadré ni d'aplat coloré — on
 * reste dans le registre sobre du site.
 */
export default function KeyFigure({
  value,
  ci,
  unit = "%",
  label,
  sample,
}: {
  value: string;
  ci?: string;
  unit?: string;
  label: string;
  sample?: string;
}) {
  return (
    <aside className="not-prose my-7 border-l-2 border-accent/60 pl-4 sm:pl-5">
      <div className="text-3xl font-semibold leading-tight sm:text-4xl">
        <Stat value={value} ci={ci} unit={unit} />
      </div>
      <p className="mt-1.5 max-w-prose text-sm text-zinc-400">
        {label}
        {sample && (
          <span className="text-zinc-500"> · {sample}</span>
        )}
      </p>
    </aside>
  );
}
