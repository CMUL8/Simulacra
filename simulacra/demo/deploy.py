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
