import Link from "next/link";
import { KOFI_URL } from "@/lib/site";

export default function Footer() {
  return (
    <footer className="border-t border-zinc-800 mt-16">
      <div className="mx-auto max-w-4xl px-4 py-8 space-y-3 text-xs text-zinc-500">
        <nav className="flex flex-wrap gap-x-5 gap-y-2 text-zinc-400">
          <Link href="/methodologie" className="hover:text-zinc-200">
            Méthodologie
          </Link>
          <Link href="/a-propos" className="hover:text-zinc-200">
            À propos
          </Link>
          <Link href="/mentions-legales" className="hover:text-zinc-200">
            Mentions légales
          </Link>
          <a href="/rss.xml" className="hover:text-zinc-200">
            Flux RSS
          </a>
          <a
            href={KOFI_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent hover:text-zinc-200"
          >
            Soutenir sur Ko-fi
          </a>
        </nav>
        {/* Boilerplate légal Riot obligatoire — texte exact, ne pas traduire. */}
        <p>
          EloLab isn&apos;t endorsed by Riot Games and doesn&apos;t reflect the views or
          opinions of Riot Games or anyone officially involved in producing or managing Riot
          Games properties. Riot Games, and all associated properties are trademarks or
          registered trademarks of Riot Games, Inc.
        </p>
        <p>
          Données collectées via l&apos;API officielle Riot (matchs ranked solo, file 420).
          Voir la{" "}
          <Link href="/methodologie" className="underline hover:text-zinc-300">
            méthodologie
          </Link>{" "}
          pour les limites connues.
        </p>
      </div>
    </footer>
  );
}
