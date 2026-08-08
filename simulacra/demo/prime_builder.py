"""Builder — customize the scaffolded app under design brief + data-viz craft."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .design_brief import apply_brief_css_tokens, brief_to_prime_block, resolve_palette
from .events import emit_event
from .prime_hook import prime_enabled
from .prime_session import prime_run
from .runs import load_state

_SKILL_PATH = Path(__file__).resolve().parent / "skills" / "data_viz.md"

BUILD_TASK = """You are building an internal data app. Taste and visualization are the product.

## User goal
{prompt}

## Data already prepared
- `public/data.json` — extracted findings ({row_count} rows)
- `public/analytics.json` — KPIs / charts
- `public/config.json` — title/subtitle
- `public/design_brief.json` — aesthetics (OBEY)
- `src/App.tsx` + `src/styles.css` — current app (edit these)

{design_block}

## Visualization craft (memorize and apply)
{viz_skill}

## Your job (all required)
1. Read analytics.json + design brief + current App.tsx / styles.css
2. Edit `src/styles.css` so palette tokens match the brief — including --muted, --border, --panel-2 (panel-2 MUST differ from panel so bar tracks show)
3. Edit `src/App.tsx` so layout + hero viz feel bespoke — not stock cyan / not one loud accent KPI card
4. Fix contrast: body text readable, theme/vendor labels not black-on-black
5. Update `public/config.json` title/subtitle from the brief if needed
6. Keep valid React/TypeScript; stay in this directory
7. Do NOT start servers or npm install
8. Make durable file edits — narration without diffs is a failed build

Impress the user. If risk bars have empty middles, text sticks to edges, or one KPI is flood-filled, you are not done.
"""


def _load_viz_skill() -> str:
	try:
		return _SKILL_PATH.read_text()[:3500]
	except OSError:
		return "Prefer ranked bars and KPI strips; encode risk with one hue family; no emoji."


def _src_fingerprint(app_dir: Path) -> str:
	h = hashlib.sha256()
	for rel in ("src/App.tsx", "src/styles.css", "public/config.json"):
		path = app_dir / rel
		if path.is_file():
			h.update(rel.encode())
			h.update(path.read_bytes())
	return h.hexdigest()


def _force_style_pass(app_dir: Path, project_id: str) -> bool:
	"""Guaranteed aesthetic write from brief when the agent leaves files untouched."""
	state = load_state(project_id)
	brief = state.design_brief or {}
	changed = apply_brief_css_tokens(app_dir, brief)
	palette = resolve_palette(brief)
	cfg_path = app_dir / "public" / "config.json"
	if cfg_path.is_file():
		try:
			data = json.loads(cfg_path.read_text())
		except json.JSONDecodeError:
			data = {}
		before = json.dumps(data, sort_keys=True)
		if brief.get("product_name"):
			data["title"] = str(brief["product_name"])[:80]
		if brief.get("one_liner"):
			data["subtitle"] = str(brief["one_liner"])[:120]
		data["accent"] = palette.get("accent")
		after = json.dumps(data, sort_keys=True)
		if after != before:
			cfg_path.write_text(json.dumps(data, indent=2))
			changed = True
	# Stamp styles.css so fingerprint always moves when we intend a style pass
	css_path = app_dir / "src" / "styles.css"
	if css_path.is_file():
		css = css_path.read_text()
		stamp = f"/* style_pass:{palette.get('accent', '')} */\n"
		if stamp not in css:
			css_path.write_text(stamp + css)
			changed = True
		elif "/* style_pass:" in css:
			import re

			new_css = re.sub(r"/\* style_pass:[^*]*\*/\n?", stamp, css, count=1)
			if new_css != css:
				css_path.write_text(new_css)
				changed = True
	return changed


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
		# Still apply styles so the user sees brief take effect
		forced = _force_style_pass(app_dir, project_id)
		return {
			"used": False,
			"ok": False,
			"source": "heuristic",
			"error": "builder_off",
			"files_changed": forced,
			"style_only": True,
		}

	state = load_state(project_id)
	design_block = brief_to_prime_block(state.design_brief or {}, delta_note=delta_note)
	task = BUILD_TASK.format(
		prompt=prompt,
		row_count=row_count,
		design_block=design_block,
		viz_skill=_load_viz_skill(),
	)
	timeout = 300.0 if kind == "build_run" else 200.0

	emit_event(
		project_id,
		"phase",
		label="Building app",
		detail="Customizing layout, style, and charts",
		status="running",
	)

	# Apply tokens first so the agent starts on-brand
	apply_brief_css_tokens(app_dir, state.design_brief or {})
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

	if meta.get("error") and not changed:
		_force_style_pass(app_dir, project_id)
		meta["ok"] = False
		meta["source"] = "error"
		meta["style_only"] = True
		emit_event(
			project_id,
			"think",
			label="Build incomplete",
			detail=str(meta.get("error"))[:200],
			status="fail",
		)
	elif not changed:
		forced = _force_style_pass(app_dir, project_id)
		meta["ok"] = False
		meta["source"] = "heuristic"
		meta["error"] = "no_file_changes"
		meta["style_only"] = True
		meta["files_changed"] = forced
		emit_event(
			project_id,
			"think",
			label="Styles applied — layout unchanged",
			detail="Builder did not edit App.tsx; brief styles were applied",
			status="done",
		)
	elif meta.get("ok"):
		meta["source"] = "prime"
		meta["style_only"] = False
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
			label="Build incomplete",
			detail=meta.get("error") or "incomplete",
			status="done",
		)
	return meta
