#!/usr/bin/env bash
# Restart API + console under a daemonized supervisor (always keep both up).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> Stopping old servers / supervisor"
if [[ -f /tmp/simulacra-keep.pid ]]; then
  kill "$(cat /tmp/simulacra-keep.pid)" 2>/dev/null || true
  rm -f /tmp/simulacra-keep.pid
fi
lsof -ti:8000 2>/dev/null | xargs kill -9 2>/dev/null || true
lsof -ti:5173 2>/dev/null | xargs kill -9 2>/dev/null || true
pkill -f 'uvicorn apps.api.main:app' 2>/dev/null || true
pkill -f 'scripts/keep_demo.py' 2>/dev/null || true
pkill -f 'scripts/keep-demo.sh' 2>/dev/null || true
sleep 1

# shellcheck disable=SC1091
source .venv/bin/activate
python "$ROOT/scripts/keep_demo.py" --force
