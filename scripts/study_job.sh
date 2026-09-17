#!/usr/bin/env bash
# Job cron du générateur d'études. Usage : scripts/study_job.sh weekly|tierlist
# Un seul job à la fois (flock) ; tout est journalisé dans logs/studies.log.
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1
# cron n'a pas le PATH de la session : claude est dans ~/.local/bin
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p logs data
exec flock -w 3600 data/.studies.lock \
  /usr/bin/python3 scripts/generate_study.py "$@" >> logs/studies.log 2>&1
