#!/usr/bin/env python3
"""Génère les assets de marque EloLab depuis les deux PNG sources.

    pip install pillow
    python3 scripts/build_brand_assets.py

Sources (à la racine de site/, non versionnées) :
  - elolab-logo.png         logotype « EloLab » + histogramme
  - elolab-histogramme.png  histogramme seul

Produit dans site/public/ :
  - elolab-logo.png, elolab-histogramme.png  recadrés serré, FOND TRANSPARENT
  - favicon.ico (16/32/48), apple-touch-icon.png, icon-192.png, icon-512.png
  - og.png (1200x630, fond charbon, logotype centré)

Pourquoi la transparence : les sources ont un fond #0f1014, le site est en
#09090b (bg-zinc-950). Poser les images telles quelles dessinerait un
rectangle plus clair autour du logo. Le fond est donc retiré (alpha calculé
depuis la luminance, avec dé-mélange des pixels antialiasés pour éviter un
liseré sombre), ce qui rend les images valables sur n'importe quel fond.

Les icônes destinées aux OS (favicon, apple-touch, PWA) gardent un fond
opaque : un motif blanc transparent disparaîtrait sur une barre d'onglets
claire.
"""

import os
import sys

from PIL import Image

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")
PUBLIC = os.path.join(SITE, "public")

# Fond du site : Tailwind bg-zinc-950. Doit rester synchronisé avec
# site/app/globals.css — la constante est reprise dans le README.
SITE_BG = (9, 9, 11)

MARGIN_RATIO = 0.06     # marge minimale conservée autour du motif
ALPHA_FLOOR = 6         # écart de luminance en dessous duquel c'est du fond
ALPHA_CEIL = 42         # écart au-dessus duquel c'est du motif plein


def luminance(pixel) -> float:
    r, g, b = pixel[:3]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def background_colour(im: Image.Image):
    """Couleur de fond estimée sur les quatre coins."""
    w, h = im.size
    px = im.load()
    samples = [px[x, y]
               for x in (0, 1, 2, w - 3, w - 2, w - 1)
               for y in (0, 1, 2, h - 3, h - 2, h - 1)]
    n = len(samples)
    return tuple(round(sum(s[i] for s in samples) / n) for i in range(3))


def remove_background(im: Image.Image) -> Image.Image:
    """Fond -> transparent, avec dé-mélange des bords antialiasés."""
    im = im.convert("RGB")
    w, h = im.size
    bg = background_colour(im)
    bg_lum = luminance(bg)
    src = im.load()

    out = Image.new("RGBA", (w, h))
    dst = out.load()
    span = ALPHA_CEIL - ALPHA_FLOOR
    for y in range(h):
        for x in range(w):
            pixel = src[x, y]
            delta = luminance(pixel) - bg_lum
            if delta <= ALPHA_FLOOR:
                dst[x, y] = (0, 0, 0, 0)
                continue
            if delta >= ALPHA_CEIL:
                dst[x, y] = (*pixel, 255)
                continue
            alpha = (delta - ALPHA_FLOOR) / span
            # couleur observée = motif*alpha + fond*(1-alpha) : on isole le motif
            pure = tuple(
                max(0, min(255, round((pixel[i] - bg[i] * (1 - alpha)) / alpha)))
                for i in range(3)
            )
            dst[x, y] = (*pure, round(alpha * 255))
    return out


def trim(im: Image.Image, margin_ratio: float = MARGIN_RATIO) -> Image.Image:
    """Recadre sur le motif visible, en gardant une marge minimale."""
    bbox = im.getbbox()          # sur RGBA : boîte des pixels non transparents
    if not bbox:
        return im
    left, top, right, bottom = bbox
    margin = round(max(right - left, bottom - top) * margin_ratio)
    left = max(0, left - margin)
    top = max(0, top - margin)
    right = min(im.width, right + margin)
    bottom = min(im.height, bottom + margin)
    return im.crop((left, top, right, bottom))


def on_background(im: Image.Image, size: int, fill=SITE_BG,
                  padding: float = 0.14) -> Image.Image:
    """Motif centré sur un carré opaque (icônes OS)."""
    canvas = Image.new("RGBA", (size, size), (*fill, 255))
    usable = round(size * (1 - 2 * padding))
    scaled = im.copy()
    scaled.thumbnail((usable, usable), Image.LANCZOS)
    canvas.paste(scaled,
                 ((size - scaled.width) // 2, (size - scaled.height) // 2),
                 scaled)
    return canvas


def build_og(logo: Image.Image, width=1200, height=630) -> Image.Image:
    """Carte Open Graph : fond charbon exact + logotype centré."""
    canvas = Image.new("RGB", (width, height), SITE_BG)
    scaled = logo.copy()
    scaled.thumbnail((round(width * 0.68), round(height * 0.42)), Image.LANCZOS)
    canvas.paste(scaled,
                 ((width - scaled.width) // 2, (height - scaled.height) // 2),
                 scaled)
    return canvas


def main() -> int:
    logo_src = os.path.join(SITE, "elolab-logo.png")
    mark_src = os.path.join(SITE, "elolab-histogramme.png")
    for path in (logo_src, mark_src):
        if not os.path.exists(path):
            print(f"Source manquante : {path}")
            return 1
    os.makedirs(PUBLIC, exist_ok=True)

    print("Détourage et recadrage…")
    logo = trim(remove_background(Image.open(logo_src)))
    mark = trim(remove_background(Image.open(mark_src)))
    logo.save(os.path.join(PUBLIC, "elolab-logo.png"), optimize=True)
    mark.save(os.path.join(PUBLIC, "elolab-histogramme.png"), optimize=True)
    print(f"  elolab-logo.png        {logo.width}x{logo.height} (fond transparent)")
    print(f"  elolab-histogramme.png {mark.width}x{mark.height} (fond transparent)")

    print("Icônes (fond opaque, sinon invisibles sur barre d'onglets claire)…")
    favicon = on_background(mark, 48, padding=0.10)
    favicon.convert("RGB").save(
        os.path.join(PUBLIC, "favicon.ico"), format="ICO",
        sizes=[(16, 16), (32, 32), (48, 48)])
    on_background(mark, 180, padding=0.16).convert("RGB").save(
        os.path.join(PUBLIC, "apple-touch-icon.png"), optimize=True)
    for size in (192, 512):
        on_background(mark, size, padding=0.16).convert("RGB").save(
            os.path.join(PUBLIC, f"icon-{size}.png"), optimize=True)
    print("  favicon.ico (16/32/48), apple-touch-icon.png, icon-192.png, icon-512.png")

    print("Carte Open Graph…")
    og = build_og(logo)
    og.save(os.path.join(PUBLIC, "og.png"), optimize=True)
    print(f"  og.png {og.width}x{og.height}, fond #%02x%02x%02x" % SITE_BG)

    print("\nTerminé. Fond des icônes/OG = fond du site "
          "(#%02x%02x%02x, bg-zinc-950)." % SITE_BG)
    return 0


if __name__ == "__main__":
    sys.exit(main())
