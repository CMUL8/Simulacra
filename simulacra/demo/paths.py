from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "fixtures" / "data-room"
TEMPLATE_APP = REPO_ROOT / "templates" / "internal-app"
TEMPLATES_DIR = REPO_ROOT / "templates"
RUNS_DIR = Path(os.environ.get("SIMULACRA_RUNS_DIR", REPO_ROOT / "runs")).resolve()


def ensure_runs_dir() -> Path:
	RUNS_DIR.mkdir(parents=True, exist_ok=True)
	return RUNS_DIR
