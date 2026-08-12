import type { Metadata } from "next";
import Link from "next/link";
import Footer from "@/components/Footer";
import Logo from "@/components/Logo";
import {
  FEED_ALTERNATE,
  SITE_DESCRIPTION as DESCRIPTION,
  SITE_NAME,
  SITE_TITLE as TITLE,
  SITE_URL as siteUrl,
} from "@/lib/site";
import "./globals.css";

export const metadata: Metadata = {
  // Base absolue des URLs de métadonnées : sans elle, og:image reste relative
  // et n'est pas résolue par Discord, Slack ou Twitter.
  metadataBase: new URL(siteUrl),
  title: { default: TITLE, template: `%s — ${SITE_NAME}` },
  description: DESCRIPTION,
  applicationName: SITE_NAME,
  manifest: "/manifest.webmanifest",
  // Auto-découverte du flux : <link rel="alternate" type="application/rss+xml">
  alternates: FEED_ALTERNATE,
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/icon-192.png", type: "image/png", sizes: "192x192" },
    ],
    apple: "/apple-touch-icon.png",
  },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    locale: "fr_FR",
    title: TITLE,
    description: DESCRIPTION,
    url: siteUrl,
    images: [{ url: "/og.png", width: 1200, height: 630, alt: SITE_NAME }],
  },
  twitter: {
    card: "summary_large_image",
    title: TITLE,
    description: DESCRIPTION,
    images: ["/og.png"],
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="fr">
      <body className="min-h-screen flex flex-col">
        <header className="border-b border-zinc-800">
          <div className="mx-auto max-w-4xl px-4 py-4 flex items-center justify-between">
            <Link href="/" aria-label="EloLab — accueil" className="shrink-0">
              <Logo />
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
