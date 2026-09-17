import { KOFI_URL } from "@/lib/site";

/**
 * Ligne de soutien : pas de bannière, pas de popup, aucun script tiers —
 * un simple lien vers la page Ko-fi.
 */
export default function Support() {
  return (
    <aside className="not-prose mt-12 border-t border-zinc-800 pt-5 text-sm text-zinc-400">
      EloLab est gratuit, sans publicité et sans compte. Le serveur de collecte
      coûte environ 8 €/mois — si ces études vous servent, vous pouvez{" "}
      <a
        href={KOFI_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="text-accent underline underline-offset-2 hover:text-zinc-100"
      >
        y contribuer sur Ko-fi
      </a>
      .
    </aside>
  );
}
