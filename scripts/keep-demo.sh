#!/usr/bin/env bash
# Compat wrapper — real supervisor is keep_demo.py
exec "$(cd "$(dirname "$0")" && pwd)/restart-demo.sh"
