/**
 * Traitement visuel des intervalles de confiance, pour tout le site.
 *
 * L'IC ne disparaît jamais — c'est le cœur de la promesse éditoriale — mais
 * il passe au second plan : plus petit, gris atténué. Trois traitements :
 *
 *  "crochets"  52,67 % [52,19 – 53,15]   notation statistique conservée
 *  "plage"     52,67 % 52,19–53,15       crochets retirés, plus léger
 *  "exposant"  52,67 % ±0,48             demi-largeur seule, le plus compact
 *
 * Changer cette seule constante bascule le corps de texte ET les tableaux.
 */
export type StatVariant = "crochets" | "plage" | "exposant";

export const STAT_VARIANT: StatVariant = "crochets";
