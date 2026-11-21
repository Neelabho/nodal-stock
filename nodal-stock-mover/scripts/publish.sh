#!/usr/bin/env bash
# Run the daily pipeline on the operator machine (where Ollama runs), then publish the resulting plan to the public app via git.
set -euo pipefail
cd "$(dirname "$0")/.."
(cd backend && ../.venv/bin/python -m nodal.cli daily "$@")
git add published/latest.json.gz
if git diff --cached --quiet; then echo "No new plan to publish."; exit 0; fi
git commit -m "chore: publish daily plan $(date +%Y-%m-%d)"
git push
echo "Pushed. The hosting platform redeploys automatically and serves the new plan."
