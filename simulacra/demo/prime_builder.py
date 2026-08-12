"""Builder — customize the scaffolded artifact under design brief + format craft."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .design_brief import apply_brief_css_tokens, brief_to_prime_block, resolve_palette
from .events import emit_event
from .formats import get_format, normalize_kind, skill_path
from .prime_hook import prime_enabled
from .prime_session import prime_run
from .runs import load_state

_SKILL_PATH = Path(__file__).resolve().parent / "skills" / "data_viz.md"

BUILD_TASK = """You are authoring a {format_label} ({artifact_kind}).

## User goal — you decide structure, IA, and craft for this
{prompt}

## Format (soft hint only — not a checklist)
{format_hint}

## Room inventory (facts — not a product design)
Files ready under `public/`: data.json ({row_count} rows), analytics.json, config.json,
design_brief.json (palette seed), sources.json, data_profile.json, agent_context.md.
Starter scaffold: `src/App.tsx` + `src/styles.css` — treat as disposable canvas.

{data_block}

{design_block}

## Preferences (not requirements)
{viz_skill}

## Contract
1. Author the artifact for the USER GOAL. You judge sections, KPIs, narrative, and layout.
2. Make durable edits to `src/App.tsx` and/or `src/styles.css` (required). Narration-only = fail.
3. When fetching public JSON, parse safely: if the body is empty/HTML, fall back to []/null — never crash the preview with Unexpected token '<'.
   Asset URLs must use `import.meta.env.BASE_URL` (or relative paths). Never fetch `/config.json` from the site root.
4. Use the palette from the brief when sensible; keep contrast readable.
5. Update `public/config.json` title/subtitle to match the goal.
6. Valid React/TypeScript; stay in this directory; no servers / npm install.
7. Empty room → honest empty state. Never invent records or a wrong product domain.

CRITICAL: Write files with your tools. Do not stop after only reading.
"""

STEER_RETRY = """CRITICAL RETRY — zero durable edits to `src/App.tsx`.

Write `src/App.tsx` now for the USER GOAL. You own structure and craft. Then stop.

{original_task}
"""

CONTENT_FOCUS = """CRITICAL — styles may already be updated. Do NOT spend time on CSS.

Rewrite `src/App.tsx` now for this follow-up. Prefer charts, large stats, timelines,
and comparison panels over long text cards. Durable App.tsx edits are required.
Then stop.

Follow-up:
{delta}
"""

CRAFT_MARKER = "// simulacra_craft:"


def _load_skill(kind: str | None) -> str:
	path = skill_path(kind)
	try:
		return path.read_text()[:3500]
	except OSError:
		try:
			return _SKILL_PATH.read_text()[:3500]
		except OSError:
			return "Prefer clear hierarchy and readable contrast; author for the user goal."


def _load_viz_skill() -> str:
	return _load_skill("data_app")


def _file_hash(path: Path) -> str:
	if not path.is_file():
		return ""
	return hashlib.sha256(path.read_bytes()).hexdigest()


_TRACKED_SRC = ("src/App.tsx", "src/styles.css", "public/config.json")


def _src_hashes(app_dir: Path) -> dict[str, str]:
	out: dict[str, str] = {}
	for rel in _TRACKED_SRC:
		path = app_dir / rel
		if path.is_file():
			out[rel] = _file_hash(path)
	return out


def _src_fingerprint(app_dir: Path) -> str:
	h = hashlib.sha256()
	for rel, digest in sorted(_src_hashes(app_dir).items()):
		h.update(rel.encode())
		h.update(digest.encode())
	return h.hexdigest()


def _changed_src_files(app_dir: Path, before: dict[str, str]) -> list[str]:
	after = _src_hashes(app_dir)
	changed = [rel for rel, digest in after.items() if before.get(rel) != digest]
	for rel in before:
		if rel not in after:
			changed.append(rel)
	return sorted(set(changed))


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
	"""Style-only safety net — never author IA/content in place of Prime.

	May sync research-aware report wiring. Does not rewrite section structure,
	KPI chrome, or diligence layouts. Returns True only for real research App sync.
	"""
	state = load_state(project_id)
	kind = normalize_kind(state.artifact_kind)
	brief = state.design_brief or {}
	aes = brief.get("aesthetic") or {}
	density = str(aes.get("density") or "compact")
	chrome = str(aes.get("chrome") or "no-cards").replace(" ", "-")
	direction = str(aes.get("direction") or "dense-ops")

	tsx_path = app_dir / "src" / "App.tsx"
	if not tsx_path.is_file():
		return False

	content_win = False
	if kind == "report":
		try:
			from .research_bundle import ensure_research_aware_report_app

			content_win = ensure_research_aware_report_app(app_dir)
		except Exception:  # noqa: BLE001
			content_win = False

	# Soft class hooks only — no section/KPI authorship
	before = tsx_path.read_bytes()
	tsx = before.decode("utf-8")
	stamp = f"{CRAFT_MARKER}style:{kind}:{direction}:{density}\n"
	if CRAFT_MARKER not in tsx:
		tsx = stamp + tsx
	else:
		tsx = re.sub(rf"{re.escape(CRAFT_MARKER)}[^\n]*\n?", stamp, tsx, count=1)
	for root in ("report", "deck", "sheet", "app"):
		tsx = re.sub(
			rf'className="{root}(?:\s[^"]*)?"',
			f'className="{root} density-{density} chrome-{chrome} dir-{direction}"',
			tsx,
			count=1,
		)
	if tsx.encode("utf-8") != before:
		tsx_path.write_text(tsx)

	_force_style_pass(app_dir, project_id)
	return bool(content_win)


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

	Success requires durable `src/App.tsx` changes from Prime. Without them we
	only apply style tokens — we do not invent layout/IA in place of the agent.
	"""
	if not prime_enabled():
		styled = _force_style_pass(app_dir, project_id)
		content_win = _deterministic_layout_pass(app_dir, project_id)
		return {
			"used": False,
			"ok": True,
			"source": "prime" if content_win else "style",
			"error": "builder_off",
			"files_changed": bool(styled) or content_win,
			"style_only": not content_win,
			"layout_customized": content_win,
		}

	state = load_state(project_id)
	spec = get_format(state.artifact_kind)
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
		viz_skill=_load_skill(spec.kind),
		format_label=spec.label,
		artifact_kind=spec.kind,
		format_hint=spec.aesthetic_hint,
	)
	timeout = 420.0 if kind == "build_run" else 360.0

	emit_event(
		project_id,
		"phase",
		label=f"Building {spec.short.lower()}",
		detail=f"Customizing {spec.label.lower()} from your brief",
		status="running",
	)

	# Apply tokens first so the agent starts on-brand
	apply_brief_css_tokens(app_dir, state.design_brief or {})
	before_hashes = _src_hashes(app_dir)
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
	meta["changed_files"] = _changed_src_files(app_dir, before_hashes)
	meta["write_tools"] = int(meta.get("write_tools") or 0)
	events_total = int(meta.get("events") or 0)

	# Steered retry when App.tsx still untouched — including after timeout/error,
	# otherwise CSS-only lands and chat dumps "rephrase" on the user.
	if not app_changed:
		emit_event(
			project_id,
			"think",
			label="No layout edits — retrying",
			detail="Steering builder to rewrite App.tsx (content, not just styles)",
			status="running",
		)
		retry_prompt = (
			CONTENT_FOCUS.format(delta=delta_note or prompt[:500])
			if delta_note or kind == "iterate_run"
			else STEER_RETRY.format(original_task=task)
		)
		retry_meta = prime_run(
			project_id,
			cwd=app_dir,
			prompt=retry_prompt,
			name="simulacra-builder",
			timeout=min(timeout, 300.0),
		)
		meta["events"] = events_total + int(retry_meta.get("events") or 0)
		meta["write_tools"] = int(meta.get("write_tools") or 0) + int(retry_meta.get("write_tools") or 0)
		meta["retry"] = True
		if retry_meta.get("session_id"):
			meta["session_id"] = retry_meta.get("session_id")
		if retry_meta.get("model"):
			meta["model"] = retry_meta.get("model")
		# Prefer fresh error only if retry also failed without touching App.tsx
		app_changed = _app_tsx_hash(app_dir) != before_app
		any_changed = _src_fingerprint(app_dir) != before_fp
		meta["files_changed"] = any_changed
		meta["layout_customized"] = app_changed
		meta["changed_files"] = _changed_src_files(app_dir, before_hashes)
		if app_changed:
			meta["error"] = None
		elif retry_meta.get("error"):
			meta["error"] = retry_meta.get("error")

	if app_changed:
		meta["ok"] = True
		meta["source"] = "prime"
		meta["style_only"] = False
		meta["error"] = None
		meta["changed_files"] = _changed_src_files(app_dir, before_hashes)
		emit_event(
			project_id,
			"phase",
			label="Build complete",
			detail=f"{meta.get('events', 0)} steps · App.tsx updated",
			status="done",
		)
		return meta

	# Agent still failed — style only; do not invent IA for Prime
	emit_event(
		project_id,
		"think",
		label="Keeping canvas — agent did not rewrite App.tsx",
		detail="Applied palette tokens only. Retry Build so the agent authors the artifact.",
		status="running",
	)
	crafted = _deterministic_layout_pass(app_dir, project_id)
	meta["craft_fallback"] = True
	meta["changed_files"] = _changed_src_files(app_dir, before_hashes)
	kind_now = normalize_kind(state.artifact_kind)

	if crafted:
		# Research App sync only — still not a full Prime authorship win
		meta["ok"] = True
		meta["source"] = "prime"
		meta["style_only"] = False
		meta["layout_customized"] = True
		meta["files_changed"] = True
		meta["error"] = meta.get("error") or "agent_no_app_edits"
		emit_event(
			project_id,
			"phase",
			label="Build complete",
			detail="Research wiring synced — open Preview",
			status="done",
		)
		return meta

	forced = _force_style_pass(app_dir, project_id)
	meta["changed_files"] = _changed_src_files(app_dir, before_hashes)
	files_touched = bool(meta["changed_files"]) or forced
	meta["ok"] = True
	meta["source"] = "style"
	meta["error"] = meta.get("error") or "agent_no_app_edits"
	meta["style_only"] = True
	meta["files_changed"] = files_touched
	meta["layout_customized"] = False
	emit_event(
		project_id,
		"think",
		label="Styles applied — layout unchanged",
		detail="Scaffold left as canvas. Retry Build so the agent authors the artifact.",
		status="done",
	)
	return meta
