#!/usr/bin/env bash
# Arrête proprement le collecteur (SIGTERM, arrêt gracieux des workers).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f collector.pid ]; then
    echo "Pas de collector.pid : le collecteur ne semble pas tourner."
    exit 0
fi

PID="$(cat collector.pid)"
if ! kill -0 "$PID" 2>/dev/null; then
    echo "PID $PID mort, nettoyage du fichier pid."
    rm -f collector.pid
    exit 0
fi

echo "Arrêt du collecteur (PID $PID)…"
kill "$PID"
for _ in $(seq 1 30); do
    if ! kill -0 "$PID" 2>/dev/null; then
        echo "Collecteur arrêté."
        rm -f collector.pid
        exit 0
    fi
    sleep 1
done

echo "Toujours vivant après 30s, kill -9."
kill -9 "$PID" 2>/dev/null || true
rm -f collector.pid
