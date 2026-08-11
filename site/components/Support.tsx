/**
 * Ligne de soutien : pas de bannière, pas de popup, aucun script tiers —
 * un simple lien. Si NEXT_PUBLIC_KOFI_URL est absente, le composant ne rend
 * rien du tout (aucune trace dans le HTML).
 */
export default function Support() {
  const kofi = process.env.NEXT_PUBLIC_KOFI_URL;
  if (!kofi) return null;
  return (
    <aside className="not-prose mt-12 border-t border-zinc-800 pt-5 text-sm text-zinc-400">
      EloLab est gratuit, sans publicité et sans compte. Le serveur de collecte
      coûte environ 8 €/mois — si ces études vous servent, vous pouvez{" "}
      <a
        href={kofi}
        target="_blank"
        rel="noopener noreferrer"
        className="text-accent underline underline-offset-2 hover:text-zinc-100"
      >
        y contribuer
      </a>
      .
    </aside>
  );
}
