import type { Metadata } from "next";
import Link from "next/link";
import Footer from "@/components/Footer";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "EloLab — le laboratoire de données de la Faille",
    template: "%s — EloLab",
  },
  description:
    "Études statistiques du jeu classé de League of Legends : pick, ban et winrate par rank et région, à partir d'un dataset de matchs ranked solo collecté en continu.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="min-h-screen flex flex-col">
        <header className="border-b border-zinc-800">
          <div className="mx-auto max-w-4xl px-4 py-4 flex items-baseline justify-between">
            {/* Guidelines Riot : jamais "LoL"/"League of Legends" dans le nom/logo */}
            <Link href="/" className="text-lg font-semibold text-zinc-100 hover:text-accent">
              Elo<span className="text-accent">Lab</span>
            </Link>
            <nav className="flex gap-6 text-sm">
              <Link href="/" className="hover:text-zinc-100">
                Études
              </Link>
              <Link href="/methodologie" className="hover:text-zinc-100">
                Méthodologie
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto w-full max-w-4xl flex-1 px-4 py-10">{children}</main>
        <Footer />
      </body>
    </html>
  );
}
