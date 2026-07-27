export default function Footer() {
  return (
    <footer className="border-t border-zinc-800 mt-16">
      <div className="mx-auto max-w-4xl px-4 py-8 space-y-3 text-xs text-zinc-500">
        {/* Boilerplate légal Riot obligatoire — texte exact, ne pas traduire. */}
        <p>
          EloLab isn&apos;t endorsed by Riot Games and doesn&apos;t reflect the views or
          opinions of Riot Games or anyone officially involved in producing or managing Riot
          Games properties. Riot Games, and all associated properties are trademarks or
          registered trademarks of Riot Games, Inc.
        </p>
        <p>
          Données collectées via l&apos;API officielle Riot (matchs ranked solo, file 420).
          Voir la <a href="/methodologie" className="underline hover:text-zinc-300">méthodologie</a>{" "}
          pour les limites connues.
        </p>
      </div>
    </footer>
  );
}
