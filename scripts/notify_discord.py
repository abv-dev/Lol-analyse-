#!/usr/bin/env python3
"""Annonce les nouvelles études EloLab sur Discord via un webhook.

Appelé après publication d'une étude (merge + déploiement). Poste un embed
par étude : titre, chapô, chiffre-clé, image OG, lien.

    python3 scripts/notify_discord.py                 # annonce ce qui ne l'a pas été
    python3 scripts/notify_discord.py --dry-run       # affiche le payload, n'envoie rien
    python3 scripts/notify_discord.py --study tierlist/16-15
    python3 scripts/notify_discord.py --init          # marque tout comme annoncé, sans envoi

Aucune dépendance hors stdlib (urllib), comme le reste des scripts du repo.

URL du webhook : DISCORD_WEBHOOK_URL, lue dans l'environnement ou dans .env.
Absente => le script ne fait rien et sort en 0 (le pipeline de publication ne
doit pas casser parce que la diffusion Discord n'est pas configurée). L'URL
n'est jamais écrite en dur ni journalisée : elle vaut mot de passe.

IDEMPOTENCE — une étude déjà annoncée ne l'est pas deux fois. L'état vit dans
data/notify_discord.json (comme data/milestones_done.json), écrit juste après
chaque envoi réussi, pas à la fin du lot : une coupure au milieu d'un rattrapage
ne fait pas ré-annoncer ce qui est déjà parti.

Cet état est local à la machine (data/ est gitignoré). Sur une machine neuve,
le fichier est absent et TOUTES les études passeraient pour nouvelles — d'où
le garde-fou : au premier passage, si plus d'une étude est en attente, le
script refuse d'envoyer et propose --init (tout marquer comme annoncé) ou
--force (les envoyer réellement).
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from lolcollector.config import load_env  # noqa: E402

CONTENT_DIR = os.path.join(REPO, "site", "content", "etudes")
DEFAULT_STATE = os.path.join(REPO, "data", "notify_discord.json")
DEFAULT_SITE_URL = "https://elolab.vercel.app"
ACCENT_COLOR = 0xC8AA6E  # accent doré du site (tailwind.config.ts)

# Limites Discord (au-delà, l'API renvoie 400 avec un message peu lisible).
MAX_TITLE = 256
MAX_DESCRIPTION = 4096
MAX_FIELD_VALUE = 1024
MAX_FOOTER = 2048

CHAPO_RE = re.compile(r"<Chapo>(.*?)</Chapo>", re.S)
KEYFIGURE_RE = re.compile(r"<KeyFigure\b([^>]*?)/?>", re.S)
ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
STAT_RE = re.compile(r'<Stat\b([^>]*?)/?>', re.S)


# ---------------------------------------------------------------------------
# Lecture des études
# ---------------------------------------------------------------------------

def attrs(raw: str) -> dict:
    return {k: html.unescape(v) for k, v in ATTR_RE.findall(raw)}


def strip_markdown(text: str) -> str:
    """MDX -> texte lisible dans un embed Discord.

    Discord comprend une partie du markdown mais pas le JSX : un <Stat/> laissé
    tel quel s'afficherait en clair dans l'annonce.
    """
    # <Stat value="52,67" ci="…" /> -> "52,67 %"
    def stat(match):
        a = attrs(match.group(1))
        return f"{a.get('value', '')} {a.get('unit', '%')}".strip()

    text = STAT_RE.sub(stat, text)
    text = re.sub(r"<[^>]+>", "", text)            # toute autre balise JSX
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # liens markdown
    text = text.replace("`", "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.S)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text, flags=re.S)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:  # ne pas couper au milieu d'un mot si évitable
        cut = cut[:space]
    return cut.rstrip(" ,;:") + "…"


def list_studies() -> list:
    """[(famille, patch_slug, meta, source)], plus récentes en premier."""
    studies = []
    if not os.path.isdir(CONTENT_DIR):
        return studies
    for family in sorted(os.listdir(CONTENT_DIR)):
        fam_dir = os.path.join(CONTENT_DIR, family)
        if not os.path.isdir(fam_dir):
            continue
        for slug in sorted(os.listdir(fam_dir)):
            dir_path = os.path.join(fam_dir, slug)
            meta_path = os.path.join(dir_path, "meta.json")
            mdx_path = os.path.join(dir_path, "index.mdx")
            if not (os.path.exists(meta_path) and os.path.exists(mdx_path)):
                continue
            with open(meta_path, encoding="utf-8") as fh:
                meta = json.load(fh)
            with open(mdx_path, encoding="utf-8") as fh:
                source = fh.read()
            studies.append((family, slug, meta, source))
    studies.sort(key=lambda s: s[2].get("date", ""), reverse=True)
    return studies


# ---------------------------------------------------------------------------
# Embed
# ---------------------------------------------------------------------------

def build_embed(family: str, slug: str, meta: dict, source: str, site_url: str) -> dict:
    url = f"{site_url}/etudes/{family}/{slug}"

    chapo_match = CHAPO_RE.search(source)
    # Pas de <Chapo> (étude ancienne ou format différent) : la description du
    # meta.json fait un repli honnête, c'est déjà le texte de partage OG.
    raw = chapo_match.group(1) if chapo_match else meta.get("description", "")
    description = truncate(strip_markdown(raw), MAX_DESCRIPTION)

    embed = {
        "title": truncate(meta.get("title", f"{family} {slug}"), MAX_TITLE),
        "url": url,
        "description": description,
        "color": ACCENT_COLOR,
        "image": {"url": f"{site_url}/og.png"},
        "fields": [],
    }

    kf = KEYFIGURE_RE.search(source)
    if kf:
        a = attrs(kf.group(1))
        # Chiffre, IC et taille d'échantillon collés (charte éditoriale : un
        # chiffre ne circule jamais sans les deux), la phrase en dessous.
        value = f"**{a.get('value', '')} {a.get('unit', '%')}**".strip()
        if a.get("ci"):
            value += f" `[{a['ci']}]`"
        if a.get("sample"):
            value += f" · {a['sample']}"
        label = strip_markdown(a.get("label", ""))
        if label:
            value += f"\n{label}"
        embed["fields"].append(
            {"name": "Chiffre-clé", "value": truncate(value, MAX_FIELD_VALUE)}
        )

    sample = meta.get("sample_size")
    footer = f"Patch {meta.get('patch', '?')}"
    if isinstance(sample, int):
        # Séparateur de milliers à la française : "786 509", pas "786,509".
        footer += " · {} matchs analysés".format(f"{sample:,}".replace(",", " "))
    embed["footer"] = {"text": truncate(footer, MAX_FOOTER)}
    if meta.get("date"):
        embed["timestamp"] = f"{meta['date']}T12:00:00.000Z"
    return embed


def build_payload(embed: dict) -> dict:
    return {"username": "EloLab", "embeds": [embed]}


# ---------------------------------------------------------------------------
# Envoi
# ---------------------------------------------------------------------------

class DiscordError(RuntimeError):
    pass


def post(webhook_url: str, payload: dict, attempts: int = 3) -> None:
    """POST du webhook, avec respect du 429 (Retry-After).

    Discord répond 204 sans corps quand tout va bien.
    """
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(1, attempts + 1):
        request = urllib.request.Request(
            webhook_url, data=body, method="POST",
            headers={"Content-Type": "application/json", "User-Agent": "EloLab/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                if response.status in (200, 204):
                    return
                raise DiscordError(f"réponse inattendue HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code == 429 and attempt < attempts:
                try:
                    wait = float(json.loads(detail).get("retry_after", 2))
                except (ValueError, AttributeError):
                    wait = float(exc.headers.get("Retry-After") or 2)
                print(f"    429, nouvelle tentative dans {wait:.1f} s")
                time.sleep(min(wait, 60))
                continue
            if exc.code >= 500 and attempt < attempts:
                time.sleep(2 * attempt)
                continue
            # L'URL contient le secret du webhook : ne jamais la remonter.
            raise DiscordError(f"HTTP {exc.code} — {detail}") from None
        except urllib.error.URLError as exc:
            if attempt < attempts:
                time.sleep(2 * attempt)
                continue
            raise DiscordError(f"réseau : {exc.reason}") from None
    raise DiscordError("échec après plusieurs tentatives")


# ---------------------------------------------------------------------------
# État
# ---------------------------------------------------------------------------

def load_state(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh).get("notified", {})


def save_state(path: str, notified: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"notified": notified}, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # écriture atomique : pas d'état tronqué


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--study", help="annonce une seule étude, ex tierlist/16-15")
    parser.add_argument("--dry-run", action="store_true",
                        help="affiche le payload sans rien envoyer")
    parser.add_argument("--force", action="store_true",
                        help="ré-annonce même une étude déjà annoncée")
    parser.add_argument("--init", action="store_true",
                        help="marque toutes les études comme annoncées, sans envoi")
    parser.add_argument("--state", default=DEFAULT_STATE, help="fichier d'état")
    args = parser.parse_args()

    load_env()
    site_url = (os.environ.get("SITE_URL")
                or os.environ.get("NEXT_PUBLIC_SITE_URL")
                or DEFAULT_SITE_URL).rstrip("/")
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

    studies = list_studies()
    if not studies:
        print(f"Aucune étude trouvée dans {CONTENT_DIR}.")
        return 0

    state_existed = os.path.exists(args.state)
    notified = load_state(args.state)

    if args.init:
        for family, slug, meta, _ in studies:
            notified.setdefault(f"{family}/{slug}",
                                {"title": meta.get("title", ""), "at": "init"})
        save_state(args.state, notified)
        print(f"{len(notified)} étude(s) marquée(s) comme annoncées dans {args.state} "
              f"(aucun envoi).")
        return 0

    if args.study:
        studies = [s for s in studies if f"{s[0]}/{s[1]}" == args.study]
        if not studies:
            print(f"Étude « {args.study} » introuvable.", file=sys.stderr)
            return 1

    pending = [s for s in studies if args.force or f"{s[0]}/{s[1]}" not in notified]
    if not pending:
        print("Rien à annoncer : toutes les études le sont déjà.")
        return 0

    # Garde-fou premier passage : un état absent ferait passer tout
    # l'historique pour du neuf et noierait le salon Discord.
    if not state_existed and len(pending) > 1 and not (args.force or args.dry_run):
        print(
            f"État absent ({args.state}) et {len(pending)} études non annoncées.\n"
            f"Refus d'envoyer un historique complet d'un coup. Au choix :\n"
            f"  --init    marquer les {len(pending)} comme annoncées, sans rien envoyer\n"
            f"  --force   les annoncer réellement, toutes\n"
            f"  --study famille/patch   n'en annoncer qu'une",
            file=sys.stderr,
        )
        return 1

    if not webhook and not args.dry_run:
        print("DISCORD_WEBHOOK_URL absente : aucune annonce envoyée. "
              "Renseigne-la dans .env pour activer la diffusion Discord.")
        return 0

    failures = 0
    for index, (family, slug, meta, source) in enumerate(reversed(pending)):
        key = f"{family}/{slug}"
        payload = build_payload(build_embed(family, slug, meta, source, site_url))
        if args.dry_run:
            print(f"--- {key} (dry-run) ---")
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            continue
        try:
            if index:
                time.sleep(1)  # webhooks Discord : ~5 requêtes / 2 s
            post(webhook, payload)
        except DiscordError as exc:
            print(f"ÉCHEC {key} : {exc}", file=sys.stderr)
            failures += 1
            continue
        notified[key] = {"title": meta.get("title", ""),
                         "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        save_state(args.state, notified)  # après CHAQUE envoi, pas en fin de lot
        print(f"Annoncé : {key} — {meta.get('title', '')}")

    if args.dry_run:
        print(f"\n{len(pending)} étude(s) seraient annoncées "
              f"(webhook {'configuré' if webhook else 'ABSENT'}).")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
