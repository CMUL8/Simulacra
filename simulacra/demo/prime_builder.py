"""Builder — customize the scaffolded app under design brief + bounds."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .design_brief import brief_to_prime_block
from .events import emit_event
from .prime_hook import prime_enabled
from .prime_session import prime_run
from .runs import load_state

BUILD_TASK = """You are building an internal data app for enterprise users.

## User goal
{prompt}

## Data already prepared
- `public/data.json` — extracted findings ({row_count} rows)
- `public/analytics.json` — pre-computed KPIs and charts data
- `public/config.json` — app metadata
- `public/design_brief.json` — aesthetics & IA (OBEY)
- `src/App.tsx` + `src/styles.css` — React dashboard scaffold

{design_block}

## Your job
1. Read the data files and design brief
2. You MUST edit `src/App.tsx` and/or `src/styles.css` (and `public/config.json` title/subtitle if needed) so the app matches the user goal
3. Improve titles, KPI labels, filters, and layout — bespoke, not generic
4. Keep valid React/TypeScript — do not break the build
5. Stay inside this directory only
6. Do NOT start preview servers or install packages
7. Do NOT re-read the same file more than twice; stop when brief is satisfied

Make real file edits. Narration without edits is a failed build.
"""


def _src_fingerprint(app_dir: Path) -> str:
	h = hashlib.sha256()
	for rel in ("src/App.tsx", "src/styles.css", "public/config.json"):
		path = app_dir / rel
		if path.is_file():
			h.update(rel.encode())
			h.update(path.read_bytes())
	return h.hexdigest()


def prime_build_app(
	app_dir: Path,
	prompt: str,
	*,
	project_id: str,
	row_count: int,
	delta_note: str = "",
	kind: str = "build_run",
) -> dict[str, Any]:
	"""Customize the scaffolded app. Returns metadata; never raises."""
	if not prime_enabled():
		return {"used": False, "ok": False, "source": "heuristic", "error": "builder_off"}

	state = load_state(project_id)
	design_block = brief_to_prime_block(state.design_brief or {}, delta_note=delta_note)
	task = BUILD_TASK.format(prompt=prompt, row_count=row_count, design_block=design_block)
	timeout = 240.0 if kind == "build_run" else 180.0

	emit_event(
		project_id,
		"phase",
		label="Building app",
		detail="Customizing under your design brief",
		status="running",
	)

	before = _src_fingerprint(app_dir)
	meta = prime_run(
		project_id,
		cwd=app_dir,
		prompt=task,
		name="simulacra-builder",
		timeout=timeout,
	)
	after = _src_fingerprint(app_dir)
	changed = before != after
	meta["files_changed"] = changed

	if meta.get("error"):
		meta["ok"] = False
		meta["source"] = meta.get("source") or "error"
		emit_event(
			project_id,
			"think",
			label="Build incomplete",
			detail=str(meta.get("error"))[:200],
			status="fail",
		)
	elif not changed:
		# Agent ran but left scaffold untouched — not a successful customize
		meta["ok"] = False
		meta["source"] = "heuristic"
		meta["error"] = meta.get("error") or "no_file_changes"
		emit_event(
			project_id,
			"think",
			label="Keeping draft (no code changes)",
			detail="Builder finished without editing the app",
			status="done",
		)
	elif meta.get("ok"):
		meta["source"] = "prime"
		emit_event(
			project_id,
			"phase",
			label="Build complete",
			detail=f"{meta.get('events', 0)} steps · files updated",
			status="done",
		)
	else:
		meta["source"] = meta.get("source") or "error"
		emit_event(
			project_id,
			"think",
			label="Keeping draft (build incomplete)",
			detail=meta.get("error") or "incomplete",
			status="done",
		)
	return meta
