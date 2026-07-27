import type { Config } from "tailwindcss";
import typography from "@tailwindcss/typography";

// Thème sombre unique (public gamer) : pas de bascule clair/sombre,
// les couleurs sombres sont appliquées directement.
const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./content/**/*.mdx",
  ],
  theme: {
    extend: {
      colors: {
        accent: { DEFAULT: "#c8aa6e", dim: "#7a6a45" },
      },
    },
  },
  plugins: [typography],
};

export default config;
