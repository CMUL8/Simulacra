"""Prime Agent builder — customize the generated app under design brief + bounds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .design_brief import brief_to_prime_block
from .events import emit_event
from .prime_hook import prime_enabled
from .prime_session import prime_run
from .runs import load_state

BUILD_TASK = """You are Simulacra, building an internal data app for enterprise users.

## User goal
{prompt}

## Data already prepared
- `public/data.json` — extracted findings ({row_count} rows)
- `public/analytics.json` — pre-computed KPIs and charts data
- `public/config.json` — app metadata
- `public/design_brief.json` — aesthetics & IA (OBEY)
- `src/App.tsx` + `src/styles.css` — React dashboard template

{design_block}

## Your job
1. Read the data files and design brief
2. Edit `src/App.tsx` and/or `src/styles.css` to match the user goal AND design brief
3. Improve titles, KPI labels, filters, and layout — bespoke, not generic
4. Keep valid React/TypeScript — do not break the build
5. Stay inside this directory only
6. Do NOT start preview servers or install packages
7. Do NOT re-read the same file more than twice; stop when brief is satisfied

Do NOT explain at length — make the code changes. The integration control layer handles data access.
"""


def prime_build_app(
	app_dir: Path,
	prompt: str,
	*,
	project_id: str,
	row_count: int,
	delta_note: str = "",
	kind: str = "build_run",
) -> dict[str, Any]:
	"""Run Prime to customize the scaffolded app. Returns metadata; never raises."""
	if not prime_enabled():
		return {"used": False, "ok": False, "source": "heuristic"}

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

	meta = prime_run(
		project_id,
		cwd=app_dir,
		prompt=task,
		name="simulacra-builder",
		timeout=timeout,
	)

	if meta.get("ok"):
		emit_event(
			project_id,
			"phase",
			label="Build complete",
			detail=f"{meta.get('events', 0)} steps",
			status="done",
		)
	elif meta.get("used"):
		emit_event(
			project_id,
			"think",
			label="Keeping draft (build incomplete)",
			detail=meta.get("error") or "no durable changes",
			status="done",
		)
	return meta
