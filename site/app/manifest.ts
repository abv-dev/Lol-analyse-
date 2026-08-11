import type { MetadataRoute } from "next";

// Fond identique à celui du site (bg-zinc-950) : sinon l'écran de démarrage
// PWA afficherait un aplat plus clair que les pages.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "EloLab — le laboratoire de données de la Faille",
    short_name: "EloLab",
    description:
      "Études statistiques du jeu classé : pick, ban et winrate par rank et région.",
    start_url: "/",
    display: "standalone",
    background_color: "#09090b",
    theme_color: "#09090b",
    lang: "fr",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
      {
        src: "/apple-touch-icon.png",
        sizes: "180x180",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
