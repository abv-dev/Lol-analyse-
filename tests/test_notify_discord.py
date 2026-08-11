#!/usr/bin/env python3
"""Tests offline de l'annonce Discord (scripts/notify_discord.py).

Un vrai serveur HTTP local tient lieu de webhook Discord : on vérifie ce qui
part réellement sur le réseau, pas seulement ce que le script prétend faire.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.environ.setdefault("RIOT_API_KEY", "x")

spec = importlib.util.spec_from_file_location(
    "notify_discord", os.path.join(REPO, "scripts", "notify_discord.py"))
notify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notify)

W = tempfile.mkdtemp(prefix="elolab-notify-")


# --- faux Discord -----------------------------------------------------------

RECEIVED = []
FAIL_ONCE_429 = {"armed": False}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if FAIL_ONCE_429["armed"]:
            FAIL_ONCE_429["armed"] = False
            payload = json.dumps({"retry_after": 0.1}).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        RECEIVED.append(json.loads(body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
WEBHOOK = f"http://127.0.0.1:{server.server_port}/api/webhooks/1/s3cr3t-token"


# --- études de test ---------------------------------------------------------

MDX = """# Tier list — patch 16.15

<StudyMeta />

<Chapo>
**Le résultat le plus net :** les joueurs choisissent à l'inverse des
chiffres, et [Yuumi](/etudes/x) à <Stat value="35,17" ci="34,9 - 35,4" />
reste `très` jouée.
</Chapo>

Du texte de corps.

<KeyFigure value="49,80" ci="49,55 - 50,05" label="Winrate de **Kai'Sa**." sample="150 427 parties" />
"""


def write_study(family, slug, title, date, patch, sample=786509, mdx=MDX):
    d = os.path.join(notify.CONTENT_DIR, family, slug)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump({"title": title, "description": "Desc.", "date": date,
                   "patch": patch, "sample_size": sample}, fh)
    with open(os.path.join(d, "index.mdx"), "w", encoding="utf-8") as fh:
        fh.write(mdx)


notify.CONTENT_DIR = os.path.join(W, "content", "etudes")
STATE = os.path.join(W, "state.json")
write_study("tierlist", "16-15", "Tier list — patch 16.15", "2026-08-11", "16.15")


def run(*argv, webhook=WEBHOOK, site="https://elolab.test"):
    os.environ["DISCORD_WEBHOOK_URL"] = webhook
    os.environ["SITE_URL"] = site
    sys.argv = ["notify_discord.py", "--state", STATE, *argv]
    return notify.main()


# --- 1) contenu de l'embed --------------------------------------------------

assert run() == 0
assert len(RECEIVED) == 1, RECEIVED
embed = RECEIVED[0]["embeds"][0]
assert embed["title"] == "Tier list — patch 16.15"
assert embed["url"] == "https://elolab.test/etudes/tierlist/16-15"
assert embed["image"]["url"] == "https://elolab.test/og.png"
assert embed["footer"]["text"] == "Patch 16.15 · 786 509 matchs analysés"

# le chapô est nettoyé : ni JSX, ni markdown, ni backticks, ni URL de lien
desc = embed["description"]
for forbidden in ("<Stat", "<Chapo", "**", "`", "](", "/etudes/x"):
    assert forbidden not in desc, (forbidden, desc)
assert "35,17 %" in desc, desc          # le <Stat/> est rendu, pas supprimé
assert "Yuumi" in desc                  # le libellé du lien est conservé
assert "\n" not in desc                 # replié sur une ligne
print("OK  embed : titre, lien, image, footer, chapô nettoyé")

field = embed["fields"][0]["value"]
assert field.startswith("**49,80 %** `[49,55 - 50,05]` · 150 427 parties"), field
assert "Winrate de Kai'Sa." in field    # markdown du label retiré
print("OK  chiffre-clé : valeur, IC et échantillon collés, libellé nettoyé")

# --- 2) idempotence ---------------------------------------------------------

assert run() == 0
assert len(RECEIVED) == 1, "une étude déjà annoncée est repartie"
assert json.load(open(STATE))["notified"]["tierlist/16-15"]["at"].endswith("Z")
print("OK  idempotence : deuxième passage, aucun envoi")

assert run("--force") == 0
assert len(RECEIVED) == 2
print("OK  --force ré-annonce explicitement")

# --- 3) webhook absent ------------------------------------------------------

write_study("tierlist", "16-16", "Tier list — patch 16.16", "2026-08-25", "16.16")
before = len(RECEIVED)
assert run(webhook="") == 0, "un webhook absent ne doit pas faire échouer la publication"
assert len(RECEIVED) == before
assert "tierlist/16-16" not in json.load(open(STATE))["notified"]
print("OK  DISCORD_WEBHOOK_URL absente : sortie 0, aucun envoi, état intact")

# --- 4) une seule nouvelle étude passe, pas l'ancienne -----------------------

assert run() == 0
assert len(RECEIVED) == before + 1
assert RECEIVED[-1]["embeds"][0]["title"] == "Tier list — patch 16.16"
print("OK  seule la nouvelle étude est annoncée")

# --- 5) 429 : retenté, pas perdu -------------------------------------------

FAIL_ONCE_429["armed"] = True
before = len(RECEIVED)
assert run("--study", "tierlist/16-15", "--force") == 0
assert len(RECEIVED) == before + 1, "l'annonce a été perdue après un 429"
print("OK  429 : Retry-After respecté puis renvoi")

# --- 6) garde-fou premier passage sur machine neuve -------------------------

FRESH = os.path.join(W, "fresh.json")
os.environ["DISCORD_WEBHOOK_URL"] = WEBHOOK
sys.argv = ["notify_discord.py", "--state", FRESH]
before = len(RECEIVED)
assert notify.main() == 1, "un état absent + 2 études doit être refusé"
assert len(RECEIVED) == before
assert not os.path.exists(FRESH)
print("OK  état absent + backlog : refus d'envoyer l'historique d'un coup")

sys.argv = ["notify_discord.py", "--state", FRESH, "--init"]
assert notify.main() == 0
assert len(RECEIVED) == before, "--init ne doit rien envoyer"
assert set(json.load(open(FRESH))["notified"]) == {"tierlist/16-15", "tierlist/16-16"}
sys.argv = ["notify_discord.py", "--state", FRESH]
assert notify.main() == 0 and len(RECEIVED) == before
print("OK  --init marque sans envoyer, le passage suivant n'a rien à faire")

# --- 7) le secret ne fuit pas ----------------------------------------------

payload_dump = json.dumps(RECEIVED, ensure_ascii=False)
assert "s3cr3t-token" not in payload_dump
source = open(os.path.join(REPO, "scripts", "notify_discord.py"), encoding="utf-8").read()
assert "discord.com/api/webhooks" not in source, "URL de webhook en dur"
print("OK  aucune URL de webhook en dur, aucun secret dans les payloads")

shutil.rmtree(W, ignore_errors=True)
server.shutdown()
print("TESTS NOTIFY DISCORD OK")
