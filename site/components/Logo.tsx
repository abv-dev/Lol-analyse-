import Image from "next/image";

/**
 * Logotype de l'en-tête.
 *
 * Le « Elo » du logotype est tracé en filet très fin : réduit à la largeur
 * d'un écran mobile, il décroche (traits qui disparaissent au rendu). En
 * dessous de `sm` (640 px) on bascule donc sur l'histogramme seul + le nom en
 * texte, qui reste net à toute taille. La bascule est purement CSS : aucun
 * JavaScript, aucun saut de mise en page à l'hydratation.
 */
export default function Logo() {
  return (
    <>
      {/* mobile : histogramme + nom en texte */}
      <span className="flex items-center gap-2 sm:hidden">
        <Image
          src="/elolab-histogramme.png"
          alt=""
          aria-hidden
          width={621}
          height={609}
          className="h-6 w-auto"
          priority
        />
        <span className="text-lg font-semibold text-zinc-100">
          Elo<span className="text-accent">Lab</span>
        </span>
      </span>
      {/* à partir de sm : logotype complet */}
      <Image
        src="/elolab-logo.png"
        alt="EloLab"
        width={899}
        height={262}
        className="hidden h-7 w-auto sm:block"
        priority
      />
    </>
  );
}
