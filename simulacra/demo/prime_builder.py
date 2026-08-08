"""Builder — customize the scaffolded app under design brief + data-viz craft."""

from __future__ import annotations

import hashlib
import json
import re
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
- `public/sources.json` — source inventory + extract report
- `public/data_profile.json` — schema stats + design nuances
- `public/agent_context.md` — excerpts + inventory (READ THIS)
- `src/App.tsx` + `src/styles.css` — current app (edit these)

{data_block}

{design_block}

## Visualization craft (memorize and apply)
{viz_skill}

## Your job (all required)
1. Read data_profile.json + agent_context.md + analytics.json + design brief + current App.tsx
2. Design layout around the data nuances (severity density, vendor count, region/owner fields, score spread)
3. Edit `src/styles.css` so palette tokens match the brief — including --muted, --border, --panel-2
4. Edit `src/App.tsx` so layout + hero viz feel bespoke for THIS data — not a generic template
5. Fix contrast: body text readable, theme/vendor labels not black-on-black
6. Update `public/config.json` title/subtitle from the brief if needed
7. Keep valid React/TypeScript; stay in this directory
8. Do NOT start servers or npm install
9. Make durable file edits — narration without diffs is a failed build
10. If the room is empty, show an honest empty state — never invent vendors/findings

Impress the user. If risk bars have empty middles, text sticks to edges, or one KPI is flood-filled, you are not done.

CRITICAL: You must make durable file edits with your tools (write/edit `src/App.tsx` and/or `src/styles.css`).
Narration without file changes is a failed build. Do not stop after only reading files.
"""

STEER_RETRY = """CRITICAL RETRY — previous turn made ZERO durable edits to `src/App.tsx`.

You failed the build contract. Do this now, in order:
1. Open/edit `src/App.tsx` with your write/edit tool (required).
2. Personalize layout: root classes for density/chrome, KPI order (high risk first), section titles from the product name, brand mark.
3. Edit `src/styles.css` tokens if needed.
4. Do not only read files. Do not only narrate a plan. Stop only after App.tsx is written.

{original_task}
"""

CRAFT_MARKER = "// simulacra_craft:"


def _load_viz_skill() -> str:
	try:
		return _SKILL_PATH.read_text()[:3500]
	except OSError:
		return "Prefer ranked bars and KPI strips; encode risk with one hue family; no emoji."


def _file_hash(path: Path) -> str:
	if not path.is_file():
		return ""
	return hashlib.sha256(path.read_bytes()).hexdigest()


def _src_fingerprint(app_dir: Path) -> str:
	h = hashlib.sha256()
	for rel in ("src/App.tsx", "src/styles.css", "public/config.json"):
		path = app_dir / rel
		if path.is_file():
			h.update(rel.encode())
			h.update(path.read_bytes())
	return h.hexdigest()


def _app_tsx_hash(app_dir: Path) -> str:
	return _file_hash(app_dir / "src" / "App.tsx")


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
			new_css = re.sub(r"/\* style_pass:[^*]*\*/\n?", stamp, css, count=1)
			if new_css != css:
				css_path.write_text(new_css)
				changed = True
	return changed


def _brand_mark(direction: str) -> str:
	return {
		"dense-ops": "▣",
		"utilitarian": "▸",
		"editorial": "¶",
		"soft-minimal": "○",
		"branded-custom": "◆",
	}.get(direction, "◆")


def _deterministic_layout_pass(app_dir: Path, project_id: str) -> bool:
	"""Honest craft fallback: rewrite App.tsx layout when the agent narrates without writing."""
	state = load_state(project_id)
	brief = state.design_brief or {}
	aes = brief.get("aesthetic") or {}
	density = str(aes.get("density") or "compact")
	chrome = str(aes.get("chrome") or "no-cards").replace(" ", "-")
	direction = str(aes.get("direction") or "dense-ops")
	product = str(brief.get("product_name") or state.app_config.title or "Internal App")[:60]
	short = product.split()[0] if product.split() else "Risk"
	mark = _brand_mark(direction)

	tsx_path = app_dir / "src" / "App.tsx"
	css_path = app_dir / "src" / "styles.css"
	if not tsx_path.is_file():
		return False

	before = tsx_path.read_bytes()
	tsx = before.decode("utf-8")

	# Root layout classes from brief
	tsx = re.sub(
		r'className="app(?:\s[^"]*)?"',
		f'className="app density-{density} chrome-{chrome} dir-{direction}"',
		tsx,
		count=1,
	)

	# Brand mark
	tsx = re.sub(
		r'(<span className="brand-mark">)[^<]*(</span>)',
		rf"\g<1>{mark}\g<2>",
		tsx,
		count=1,
	)

	# Put High risk KPI first (risk-first IA for this product)
	stock_kpis = (
		'<section className="kpi-row">\n'
		'            <Kpi label="Findings" value={k.total_findings} sub="across data room" />\n'
		'            <Kpi label="Vendors" value={k.unique_vendors} sub={`${k.critical_vendors} critical`} />\n'
		'            <Kpi label="High risk" value={k.high_risk} sub={`${k.medium_risk} medium · ${k.low_risk} low`} warn />\n'
		'            <Kpi label="Avg score" value={k.avg_score} sub={`peak ${k.max_score}`} />\n'
		'            <Kpi label="Sources" value={k.source_files} sub="files ingested" />\n'
		"          </section>"
	)
	craft_kpis = (
		'<section className="kpi-row kpi-row-priority">\n'
		'            <Kpi label="High risk" value={k.high_risk} sub={`${k.medium_risk} medium · ${k.low_risk} low`} warn />\n'
		'            <Kpi label="Findings" value={k.total_findings} sub="across data room" />\n'
		'            <Kpi label="Vendors" value={k.unique_vendors} sub={`${k.critical_vendors} critical`} />\n'
		'            <Kpi label="Avg score" value={k.avg_score} sub={`peak ${k.max_score}`} />\n'
		'            <Kpi label="Sources" value={k.source_files} sub="files ingested" />\n'
		"          </section>"
	)
	if stock_kpis in tsx:
		tsx = tsx.replace(stock_kpis, craft_kpis, 1)
	elif "kpi-row-priority" not in tsx and '<section className="kpi-row">' in tsx:
		tsx = tsx.replace('<section className="kpi-row">', '<section className="kpi-row kpi-row-priority">', 1)

	# Personalized section titles
	replacements = {
		"<h2>Risk distribution</h2>": f"<h2>{short} risk mix</h2>",
		"<h2>Score distribution</h2>": "<h2>Score bands</h2>",
		"<h2>Top vendors by max risk score</h2>": f"<h2>Top vendors · {short}</h2>",
		"<h2>Theme breakdown</h2>": "<h2>Themes in play</h2>",
		"<h2>Data sources</h2>": "<h2>Sources ingested</h2>",
	}
	for old, new in replacements.items():
		tsx = tsx.replace(old, new)

	# Live meta copy
	tsx = tsx.replace(
		"Live · {k.total_findings} findings",
		f"Live · {product[:28]} · {{k.total_findings}} findings",
	)

	stamp = f"{CRAFT_MARKER}{direction}:{density}:{chrome}\n"
	if CRAFT_MARKER not in tsx:
		tsx = stamp + tsx
	else:
		tsx = re.sub(rf"{re.escape(CRAFT_MARKER)}[^\n]*\n?", stamp, tsx, count=1)

	changed = tsx.encode("utf-8") != before
	if changed:
		tsx_path.write_text(tsx)

	# Density / chrome CSS hooks
	if css_path.is_file():
		css = css_path.read_text()
		craft_css = """
/* craft_layout */
.app.density-dense .topbar { padding: 10px 16px; }
.app.density-dense .kpi { padding: 10px 12px; }
.app.density-comfortable .kpi { padding: 18px 16px; }
.app.chrome-no-cards .panel,
.app.chrome-no-cards .kpi {
  box-shadow: none;
  border-radius: 6px;
}
.app.kpi-row-priority .kpi.warn,
.kpi-row-priority .kpi.warn {
  outline: 1px solid color-mix(in srgb, var(--danger) 45%, transparent);
}
"""
		if "/* craft_layout */" not in css:
			css_path.write_text(css.rstrip() + "\n" + craft_css)
			changed = True
		elif craft_css.strip() not in css:
			css = re.sub(r"/\* craft_layout \*/[\s\S]*?(?=\n/\*|$).*", craft_css.strip() + "\n", css, count=1)
			css_path.write_text(css)
			changed = True

	_force_style_pass(app_dir, project_id)
	return changed or _app_tsx_hash(app_dir) != hashlib.sha256(before).hexdigest()


def prime_build_app(
	app_dir: Path,
	prompt: str,
	*,
	project_id: str,
	row_count: int,
	delta_note: str = "",
	kind: str = "build_run",
) -> dict[str, Any]:
	"""Customize the scaffolded app. Returns metadata; never raises.

	Success requires durable `src/App.tsx` changes. Agent narration without
	writes triggers one steered retry, then a deterministic craft personalizer.
	"""
	if not prime_enabled():
		forced_layout = _deterministic_layout_pass(app_dir, project_id)
		return {
			"used": False,
			"ok": forced_layout,
			"source": "craft" if forced_layout else "heuristic",
			"error": None if forced_layout else "builder_off",
			"files_changed": forced_layout,
			"style_only": not forced_layout,
			"layout_customized": forced_layout,
		}

	state = load_state(project_id)
	design_block = brief_to_prime_block(state.design_brief or {}, delta_note=delta_note)
	data_block = ""
	try:
		from .sources import profile_rows, sources_to_prime_block
		from .extract import ExtractReport

		preview = state.plan_preview or {}
		profile_raw = preview.get("profile")
		if profile_raw:
			from .sources import DataProfile

			profile = DataProfile(**{k: v for k, v in profile_raw.items() if k in DataProfile.__dataclass_fields__})
		else:
			# Fallback: profile from public data if present
			data_path = app_dir / "public" / "data.json"
			rows = json.loads(data_path.read_text()) if data_path.is_file() else []
			profile = profile_rows(rows if isinstance(rows, list) else [])
		extract = None
		if preview.get("extract"):
			extract = ExtractReport(
				rows=[],
				errors=list((preview.get("extract") or {}).get("errors") or []),
				skipped=list((preview.get("extract") or {}).get("skipped") or []),
			)
		data_block = sources_to_prime_block(profile, extract=extract)
	except Exception:  # noqa: BLE001
		data_block = "## Data room\n- See public/data.json and public/analytics.json"

	task = BUILD_TASK.format(
		prompt=prompt,
		row_count=row_count,
		design_block=design_block,
		data_block=data_block,
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
	before_fp = _src_fingerprint(app_dir)
	before_app = _app_tsx_hash(app_dir)

	meta = prime_run(
		project_id,
		cwd=app_dir,
		prompt=task,
		name="simulacra-builder",
		timeout=timeout,
	)
	app_changed = _app_tsx_hash(app_dir) != before_app
	any_changed = _src_fingerprint(app_dir) != before_fp
	meta["files_changed"] = any_changed
	meta["layout_customized"] = app_changed
	meta["write_tools"] = int(meta.get("write_tools") or 0)
	events_total = int(meta.get("events") or 0)

	# Steered retry when agent finishes without touching App.tsx
	if not app_changed and not meta.get("error"):
		emit_event(
			project_id,
			"think",
			label="No layout edits — retrying",
			detail="Agent finished without changing App.tsx; steering to write tools",
			status="running",
		)
		retry_prompt = STEER_RETRY.format(original_task=task)
		retry_meta = prime_run(
			project_id,
			cwd=app_dir,
			prompt=retry_prompt,
			name="simulacra-builder",
			timeout=min(timeout, 180.0),
		)
		meta["events"] = events_total + int(retry_meta.get("events") or 0)
		meta["write_tools"] = int(meta.get("write_tools") or 0) + int(retry_meta.get("write_tools") or 0)
		meta["retry"] = True
		if retry_meta.get("session_id"):
			meta["session_id"] = retry_meta.get("session_id")
		if retry_meta.get("model"):
			meta["model"] = retry_meta.get("model")
		if retry_meta.get("error") and not meta.get("error"):
			meta["error"] = retry_meta.get("error")
		app_changed = _app_tsx_hash(app_dir) != before_app
		any_changed = _src_fingerprint(app_dir) != before_fp
		meta["files_changed"] = any_changed
		meta["layout_customized"] = app_changed

	if app_changed:
		meta["ok"] = True
		meta["source"] = "prime"
		meta["style_only"] = False
		meta["error"] = None
		emit_event(
			project_id,
			"phase",
			label="Build complete",
			detail=f"{meta.get('events', 0)} steps · App.tsx updated",
			status="done",
		)
		return meta

	# Agent still failed — craft personalizer rewrites App.tsx deterministically
	emit_event(
		project_id,
		"think",
		label="Applying craft layout",
		detail="Agent did not edit App.tsx — personalizing layout from design brief",
		status="running",
	)
	crafted = _deterministic_layout_pass(app_dir, project_id)
	meta["files_changed"] = crafted or any_changed
	meta["layout_customized"] = crafted
	meta["craft_fallback"] = True

	if crafted:
		meta["ok"] = True
		meta["source"] = "craft"
		meta["style_only"] = False
		meta["error"] = meta.get("error") or "agent_no_app_edits"
		emit_event(
			project_id,
			"phase",
			label="Build complete",
			detail="Layout personalized (craft fallback)",
			status="done",
		)
		return meta

	# Last resort: styles only
	forced = _force_style_pass(app_dir, project_id)
	meta["ok"] = False
	meta["source"] = "error" if meta.get("error") else "heuristic"
	meta["error"] = meta.get("error") or "no_file_changes"
	meta["style_only"] = True
	meta["files_changed"] = forced
	meta["layout_customized"] = False
	emit_event(
		project_id,
		"think",
		label="Builder ran but did not edit layout",
		detail="No App.tsx changes — styles only. Retry Build or rephrase.",
		status="done",
	)
	return meta
