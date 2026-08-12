from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path

from .analytics import build_analytics
from .design_brief import apply_brief_css_tokens, write_brief
from .formats import get_format, normalize_kind, template_path
from .runs import AppConfig, ProjectState, load_state, project_dir


def sync_app(
	project_id: str,
	config: AppConfig,
	data_rows: list[dict],
	*,
	artifact_kind: str | None = None,
) -> Path:
	"""Copy the format template into runs/<id>/app (keeps node_modules)."""
	root = project_dir(project_id)
	app_dir = root / "app"
	kind = normalize_kind(artifact_kind)
	if artifact_kind is None:
		try:
			kind = normalize_kind(load_state(project_id).artifact_kind)
		except Exception:
			kind = "data_app"
	template = template_path(kind)
	if not template.exists():
		raise FileNotFoundError(f"Template missing for {kind}: {template}")

	app_dir.mkdir(parents=True, exist_ok=True)
	# Wipe everything except node_modules so switching formats is clean
	for child in list(app_dir.iterdir()):
		if child.name == "node_modules":
			continue
		if child.is_dir():
			shutil.rmtree(child)
		else:
			child.unlink()

	shutil.copytree(
		template,
		app_dir,
		dirs_exist_ok=True,
		ignore=shutil.ignore_patterns("node_modules", "dist"),
	)
	return refresh_app_data(project_id, config, data_rows, artifact_kind=kind)


def refresh_app_data(
	project_id: str,
	config: AppConfig,
	data_rows: list[dict],
	*,
	artifact_kind: str | None = None,
) -> Path:
	"""Update public data/config/brief without wiping agent edits to src/."""
	root = project_dir(project_id)
	app_dir = root / "app"
	if not (app_dir / "package.json").exists():
		return sync_app(project_id, config, data_rows, artifact_kind=artifact_kind)

	kind = normalize_kind(artifact_kind)
	if artifact_kind is None:
		try:
			kind = normalize_kind(load_state(project_id).artifact_kind)
		except Exception:
			kind = "data_app"
	spec = get_format(kind)

	# Derive neutral column/sort defaults from actual rows — never assume risk_score
	columns = list(config.columns or [])
	if not columns and data_rows:
		keys: list[str] = []
		seen: set[str] = set()
		for row in data_rows[:40]:
			for k in row.keys():
				if k not in seen:
					seen.add(k)
					keys.append(k)
		columns = keys[:12]
	sort_column = config.sort_column or (columns[0] if columns else "")
	highlight_column = config.highlight_column or ""

	(app_dir / "public" / "data.json").parent.mkdir(parents=True, exist_ok=True)
	(app_dir / "public" / "data.json").write_text(json.dumps(data_rows, indent=2))
	(app_dir / "public" / "analytics.json").write_text(
		json.dumps(build_analytics(data_rows), indent=2)
	)
	(app_dir / "public" / "config.json").write_text(
		json.dumps(
			{
				"title": config.title,
				"subtitle": config.subtitle,
				"layout": spec.layout,
				"artifactKind": spec.kind,
				"searchEnabled": config.search_enabled,
				"sortColumn": sort_column,
				"sortDirection": config.sort_direction,
				"groupBy": config.group_by,
				"highlightColumn": highlight_column,
				"columns": columns,
			},
			indent=2,
		)
	)
	# Keep live preview in sync — dist is what the iframe serves.
	_mirror_public_json_to_dist(app_dir)
	try:
		from .sources import profile_rows, write_agent_context

		state = load_state(project_id)
		if state.design_brief:
			write_brief(project_id, state.design_brief)
			apply_brief_css_tokens(app_dir, state.design_brief)
		profile = profile_rows(data_rows)
		write_agent_context(
			project_id,
			rows=data_rows,
			profile=profile,
			prompt=state.prompt,
		)
	except Exception:
		pass
	return app_dir


def _mirror_public_json_to_dist(app_dir: Path) -> None:
	"""Copy data/config/analytics/research JSON into dist when a build exists.

	Without this, refresh_app_data updates public/ only and the preview iframe
	(served from dist/) either serves stale JSON or SPA-fallback HTML.
	"""
	dist = app_dir / "dist"
	public = app_dir / "public"
	if not dist.is_dir() or not public.is_dir():
		return
	for name in (
		"data.json",
		"analytics.json",
		"config.json",
		"research.json",
		"design_brief.json",
	):
		src = public / name
		if not src.is_file():
			continue
		dest = dist / name
		try:
			dest.parent.mkdir(parents=True, exist_ok=True)
			shutil.copy2(src, dest)
		except OSError:
			pass


def _heal_unsafe_json_fetches(app_dir: Path) -> bool:
	"""Rewrite fragile `r.json()` boots so HTML/404 never crashes the preview."""
	tsx = app_dir / "src" / "App.tsx"
	if not tsx.is_file():
		return False
	try:
		text = tsx.read_text(encoding="utf-8")
	except OSError:
		return False
	original = text
	# Common brittle pattern: assume every 200 body is JSON
	text = text.replace(
		".then((r) => (r.ok ? r.json() : []))",
		'.then(async (r) => { if (!r.ok) return []; const t = await r.text(); const s = t.trim(); if (!s || s.startsWith("<")) return []; try { return JSON.parse(s); } catch { return []; } })',
	)
	text = text.replace(
		".then((r) => (r.ok ? r.json() : null))",
		'.then(async (r) => { if (!r.ok) return null; const t = await r.text(); const s = t.trim(); if (!s || s.startsWith("<")) return null; try { return JSON.parse(s); } catch { return null; } })',
	)
	# Bare r.json() after ok check — soften via text parse
	import re

	text = re.sub(
		r"if\s*\(\s*!r\.ok\s*\)\s*throw new Error\([^)]*\);\s*return r\.json\(\);",
		'if (!r.ok) return null; const __t = await r.text(); const __s = __t.trim(); if (!__s || __s.startsWith("<")) return null; try { return JSON.parse(__s); } catch { return null; }',
		text,
	)
	if text == original:
		return False
	try:
		tsx.write_text(text, encoding="utf-8")
	except OSError:
		return False
	return True


def _npm_install(app_dir: Path) -> None:
	subprocess.run(["npm", "install", "--silent"], cwd=app_dir, check=True)


def preview_path(project_id: str) -> str:
	"""Same-origin URL path served by the API (works on Railway + local)."""
	return f"/projects/{project_id}/preview/"


def stop_preview(state: ProjectState) -> None:
	"""Stop any legacy localhost preview process."""
	if state.preview_pid:
		try:
			os.kill(state.preview_pid, signal.SIGTERM)
		except ProcessLookupError:
			pass
		state.preview_pid = None
		state.preview_port = None


def start_preview(state: ProjectState, data_rows: list[dict], *, app_dir: Path | None = None) -> str:
	"""Build the artifact to dist/ and return a same-origin preview path."""
	stop_preview(state)
	if app_dir is None:
		app_dir = sync_app(state.id, state.app_config, data_rows, artifact_kind=state.artifact_kind)
	_heal_unsafe_json_fetches(app_dir)
	_npm_install(app_dir)

	base = preview_path(state.id)
	subprocess.run(
		["npm", "run", "build", "--silent", "--", "--base", base],
		cwd=app_dir,
		check=True,
	)
	dist = app_dir / "dist"
	if not (dist / "index.html").is_file():
		raise RuntimeError("App build produced no dist/index.html")
	try:
		from .design_brief import apply_brief_to_dist

		apply_brief_to_dist(app_dir, state.design_brief or {})
	except Exception:
		pass

	state.preview_port = None
	state.preview_pid = None
	url = base
	audit = project_dir(state.id) / "audit" / "deploy.json"
	audit.parent.mkdir(parents=True, exist_ok=True)
	audit.write_text(
		json.dumps(
			{
				"preview_url": url,
				"mode": "static",
				"artifact_kind": getattr(state, "artifact_kind", "data_app"),
				"deployed": state.deployed,
			},
			indent=2,
		)
	)
	return url
