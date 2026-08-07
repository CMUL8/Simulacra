#!/usr/bin/env bash
# Wipe all local demo project runs (safe — runs/ is gitignored)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$ROOT/runs"/proj_*
echo "Cleared all projects from runs/"
