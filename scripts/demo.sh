#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Python deps"
if [[ ! -d .venv ]]; then python3 -m venv .venv; fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -e ".[demo,dev]"

echo "==> Internal app template (npm)"
(cd templates/internal-app && npm install --silent)

echo "==> Console (npm)"
(cd apps/console && npm install --silent)

echo "==> API on :8000, Console on :5173"
trap 'kill 0' EXIT
uvicorn apps.api.main:app --host 127.0.0.1 --port 8000 --reload &
(cd apps/console && npm run dev) &
wait
