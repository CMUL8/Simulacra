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

BUILD_TASK = """You are building a {format_label} ({artifact_kind}). Taste and craft are the product.

## User goal (this is the product — obey this over any scaffold demo copy)
{prompt}

## Format
{format_hint}

## Data already prepared
- `public/data.json` — extracted rows ({row_count})
- `public/analytics.json` — derived stats (may be empty / irrelevant — do not force a dashboard)
- `public/config.json` — title/subtitle + artifactKind
- `public/design_brief.json` — aesthetics (palette/density)
- `public/sources.json` — source inventory + extract report
- `public/data_profile.json` — schema stats
- `public/agent_context.md` — excerpts + inventory (READ THIS)
- `src/App.tsx` + `src/styles.css` — starter scaffold (REWRITE for the user goal)

{data_block}

{design_block}

## Craft (memorize and apply)
{viz_skill}

## Your job (all required)
1. Read agent_context.md + user goal + current App.tsx — author for THIS topic
2. Design for THIS format — not a generic dashboard unless artifact_kind is data_app AND the data fits
3. Edit `src/styles.css` so palette tokens match the brief — including --muted, --border, --panel-2
4. Edit `src/App.tsx` so the artifact is about the user goal — discard Vendor Risk / diligence scaffold chrome if the topic is something else
5. Fix contrast: body text readable; labels not black-on-black
6. Update `public/config.json` title/subtitle from the user goal / brief
7. Keep valid React/TypeScript; stay in this directory
8. Do NOT start servers or npm install
9. Make durable file edits — narration without diffs is a failed build
10. If the room is empty, show an honest empty state — never invent records
11. Do not invent vendor/risk/findings IA unless the user asked for vendor risk or the data is clearly that shape

Impress the user. Format-specific done-when rules in the craft section are mandatory.

CRITICAL: You must make durable file edits with your tools (write/edit `src/App.tsx` and/or `src/styles.css`).
Narration without file changes is a failed build. Do not stop after only reading files.
"""

STEER_RETRY = """CRITICAL RETRY — previous turn made ZERO durable edits to `src/App.tsx`.

You failed the build contract. Do this now, in order:
1. Open/edit `src/App.tsx` with your write/edit tool (required).
2. Personalize for the chosen format and product name.
3. Edit `src/styles.css` tokens if needed.
4. Do not only read files. Do not only narrate a plan. Stop only after App.tsx is written.

{original_task}
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
			return "Prefer clear hierarchy; encode risk with one hue family; no emoji."


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
	"""Honest craft fallback when the agent narrates without writing.

	Returns True only when App.tsx meaningfully changed (data_app layout craft,
	or report App synced for research). Marker/class stamps alone return False
	after writing — callers treat that as style_only.
	"""
	state = load_state(project_id)
	kind = normalize_kind(state.artifact_kind)
	brief = state.design_brief or {}
	aes = brief.get("aesthetic") or {}
	density = str(aes.get("density") or "compact")
	chrome = str(aes.get("chrome") or "no-cards").replace(" ", "-")
	direction = str(aes.get("direction") or "dense-ops")
	product = str(brief.get("product_name") or state.app_config.title or "Internal App")[:60]
	short = product.split()[0] if product.split() else "App"
	mark = _brand_mark(direction)
	prompt_l = f"{state.prompt} {product} {state.app_config.title}".lower()
	vendor_topic = any(
		tok in prompt_l
		for tok in ("vendor", "diligence", "third-party risk", "tprm", "supplier risk")
	)

	tsx_path = app_dir / "src" / "App.tsx"
	css_path = app_dir / "src" / "styles.css"
	if not tsx_path.is_file():
		return False

	# Non-app formats: stamp craft marker + title/classes; don't regex command-center DOM
	if kind != "data_app":
		content_win = False
		if kind == "report":
			try:
				from .research_bundle import ensure_research_aware_report_app

				content_win = ensure_research_aware_report_app(app_dir)
			except Exception:  # noqa: BLE001
				content_win = False
		before = tsx_path.read_bytes()
		tsx = before.decode("utf-8")
		stamp = f"{CRAFT_MARKER}{kind}:{direction}:{density}\n"
		if CRAFT_MARKER not in tsx:
			tsx = stamp + tsx
		else:
			tsx = re.sub(rf"{re.escape(CRAFT_MARKER)}[^\n]*\n?", stamp, tsx, count=1)
		# Soft class hooks on common roots
		for root in ("report", "deck", "sheet", "app"):
			tsx = re.sub(
				rf'className="{root}(?:\s[^"]*)?"',
				f'className="{root} density-{density} chrome-{chrome} dir-{direction}"',
				tsx,
				count=1,
			)
		changed = tsx.encode("utf-8") != before
		if changed:
			tsx_path.write_text(tsx)
		_force_style_pass(app_dir, project_id)
		# Marker/class stamps are not a content win — only research App sync is
		return bool(content_win)

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

	# Put High risk KPI first only for vendor/diligence topics still on the stock scaffold
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
	if vendor_topic and stock_kpis in tsx:
		tsx = tsx.replace(stock_kpis, craft_kpis, 1)
	elif vendor_topic and "kpi-row-priority" not in tsx and '<section className="kpi-row">' in tsx:
		tsx = tsx.replace('<section className="kpi-row">', '<section className="kpi-row kpi-row-priority">', 1)

	# Personalized section titles — only when the topic is still diligence-shaped
	if vendor_topic:
		replacements = {
			"<h2>Risk distribution</h2>": f"<h2>{short} risk mix</h2>",
			"<h2>Score distribution</h2>": "<h2>Score bands</h2>",
			"<h2>Top vendors by max risk score</h2>": f"<h2>Top vendors · {short}</h2>",
			"<h2>Theme breakdown</h2>": "<h2>Themes in play</h2>",
			"<h2>Data sources</h2>": "<h2>Sources ingested</h2>",
		}
		for old, new in replacements.items():
			tsx = tsx.replace(old, new)
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
		state = load_state(project_id)
		kind = normalize_kind(state.artifact_kind)
		forced_layout = _deterministic_layout_pass(app_dir, project_id)
		# Non-data_app craft often only stamps markers — that is style_only
		if kind != "data_app" and not forced_layout:
			styled = _force_style_pass(app_dir, project_id) or True
			return {
				"used": False,
				"ok": True,
				"source": "craft",
				"error": "agent_no_app_edits",
				"files_changed": styled,
				"style_only": True,
				"layout_customized": False,
			}
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
	timeout = 300.0 if kind == "build_run" else 200.0

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
		meta["changed_files"] = _changed_src_files(app_dir, before_hashes)

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

	# Agent still failed — craft personalizer rewrites App.tsx deterministically
	emit_event(
		project_id,
		"think",
		label="Applying craft layout",
		detail="Agent did not edit App.tsx — personalizing layout from design brief",
		status="running",
	)
	crafted = _deterministic_layout_pass(app_dir, project_id)
	meta["craft_fallback"] = True
	meta["changed_files"] = _changed_src_files(app_dir, before_hashes)
	kind_now = normalize_kind(state.artifact_kind)

	if crafted:
		# Real layout/content win (data_app craft or research-aware report sync)
		meta["ok"] = True
		meta["source"] = "craft"
		meta["style_only"] = False
		meta["layout_customized"] = True
		meta["files_changed"] = True
		meta["error"] = meta.get("error") or "agent_no_app_edits"
		emit_event(
			project_id,
			"phase",
			label="Build complete",
			detail="Layout personalized from your brief",
			status="done",
		)
		return meta

	# Marker/class stamp or CSS-only — honest style_only (especially reports)
	forced = _force_style_pass(app_dir, project_id)
	files_touched = bool(meta.get("changed_files")) or forced or any_changed
	# Re-read hashes after layout pass + style (layout pass may have stamped markers)
	meta["changed_files"] = _changed_src_files(app_dir, before_hashes)
	files_touched = bool(meta["changed_files"]) or forced
	meta["ok"] = files_touched and kind_now != "data_app"
	meta["source"] = "craft" if files_touched else ("error" if meta.get("error") else "heuristic")
	meta["error"] = meta.get("error") or ("agent_no_app_edits" if files_touched else "no_file_changes")
	meta["style_only"] = True
	meta["files_changed"] = files_touched
	meta["layout_customized"] = False
	emit_event(
		project_id,
		"think",
		label="Styles applied — layout unchanged",
		detail="No content rewrite landed. Rephrase or retry Build.",
		status="done",
	)
	return meta
