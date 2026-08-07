"""Load repo-root .env into os.environ (without overwriting existing vars)."""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOADED = False


def load_dotenv() -> None:
	global _LOADED
	if _LOADED:
		return
	_LOADED = True
	env_path = _REPO_ROOT / ".env"
	if not env_path.exists():
		return
	for raw in env_path.read_text(encoding="utf-8").splitlines():
		line = raw.strip()
		if not line or line.startswith("#") or "=" not in line:
			continue
		key, _, value = line.partition("=")
		key = key.strip()
		value = value.strip().strip('"').strip("'")
		if key and key not in os.environ:
			os.environ[key] = value


def repo_root() -> Path:
	return _REPO_ROOT
