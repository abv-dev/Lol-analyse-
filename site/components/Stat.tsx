/**
 * Chiffre-clé avec son intervalle de confiance.
 *
 * L'IC ne disparaît jamais — c'est le cœur de la promesse éditoriale — mais
 * il passe au second plan visuel : plus petit, gris atténué, chiffres à
 * chasse fixe pour ne pas danser d'une ligne à l'autre.
 *
 * Trois traitements possibles, réglés en un seul endroit par STAT_VARIANT :
 *
 *  "crochets"  52,67 % [52,19 – 53,15]   crochets conservés, IC en 0,8em gris
 *  "plage"     52,67 % 52,19–53,15       crochets retirés, IC collé, plus léger
 *  "exposant"  52,67 % ±0,48             demi-largeur seule, le plus compact
 *
 * Les trois affichent la valeur exacte au survol (title) et restent lisibles
 * par un lecteur d'écran via un libellé complet.
 */

import { STAT_VARIANT, type StatVariant } from "@/lib/statVariant";

function parseBounds(ci: string): [number, number] | null {
  const parts = ci.split(/[–-]/).map((p) => Number(p.trim().replace(",", ".")));
  if (parts.length !== 2 || parts.some((n) => !Number.isFinite(n))) return null;
  return [parts[0], parts[1]];
}

function halfWidth(ci: string): string | null {
  const bounds = parseBounds(ci);
  if (!bounds) return null;
  return ((bounds[1] - bounds[0]) / 2).toFixed(2).replace(".", ",");
}

export default function Stat({
  value,
  ci,
  unit = "%",
  variant = STAT_VARIANT,
  className = "",
}: {
  value: string;
  ci?: string;
  unit?: string;
  variant?: StatVariant;
  className?: string;
}) {
  const label = ci
    ? `${value} ${unit}, intervalle de confiance à 95 % de ${ci.replace(/[–-]/, "à")}`
    : `${value} ${unit}`;

  let secondary: string | null = null;
  if (ci) {
    if (variant === "crochets") secondary = `[${ci}]`;
    else if (variant === "plage") secondary = ci.replace(/\s*[–-]\s*/, "–");
    else {
      const half = halfWidth(ci);
      secondary = half ? `±${half}` : `[${ci}]`;
    }
  }

  return (
    <span className={`whitespace-nowrap ${className}`} title={label}>
      <span className="tabular-nums text-zinc-100">
        {value}&nbsp;{unit}
      </span>
      {secondary && (
        <>
          <span className="sr-only">
            , intervalle de confiance à 95 % : {ci}
          </span>
          <span
            aria-hidden
            className="ml-1 align-baseline text-[0.8em] tabular-nums text-zinc-500"
          >
            {secondary}
          </span>
        </>
      )}
    </span>
  );
}
