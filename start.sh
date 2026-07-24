#!/usr/bin/env bash
# Lance le collecteur en arrière-plan via nohup (comme lol-live-coach).
set -euo pipefail
cd "$(dirname "$0")"

if [ -f collector.pid ] && kill -0 "$(cat collector.pid)" 2>/dev/null; then
    echo "Collecteur déjà en cours (PID $(cat collector.pid))."
    exit 1
fi

if [ ! -f .env ]; then
    echo "Fichier .env manquant. Copie .env.example vers .env et renseigne RIOT_API_KEY."
    exit 1
fi

mkdir -p data logs
nohup python3 collector.py run >> logs/nohup.out 2>&1 &
sleep 2

if [ -f collector.pid ] && kill -0 "$(cat collector.pid)" 2>/dev/null; then
    echo "Collecteur lancé (PID $(cat collector.pid)). Logs : logs/collector.log"
else
    echo "Échec du lancement, voir logs/nohup.out et logs/collector.log"
    exit 1
fi
