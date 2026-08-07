from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path

from .paths import TEMPLATE_APP
from .runs import AppConfig, ProjectState, project_dir
from .analytics import build_analytics
from .design_brief import apply_brief_css_tokens, write_brief


def _free_port() -> int:
	with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
		s.bind(("127.0.0.1", 0))
		return s.getsockname()[1]


def sync_app(project_id: str, config: AppConfig, data_rows: list[dict]) -> Path:
	root = project_dir(project_id)
	app_dir = root / "app"
	if TEMPLATE_APP.exists():
		shutil.copytree(
			TEMPLATE_APP,
			app_dir,
			dirs_exist_ok=True,
			ignore=shutil.ignore_patterns("node_modules", "dist"),
		)
	elif not (app_dir / "package.json").exists():
		raise FileNotFoundError(f"Template missing: {TEMPLATE_APP}")

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
				"layout": "command_center",
				"searchEnabled": config.search_enabled,
				"sortColumn": config.sort_column,
				"sortDirection": config.sort_direction,
				"groupBy": config.group_by,
				"highlightColumn": config.highlight_column,
				"columns": config.columns,
			},
			indent=2,
		)
	)
	# Re-apply design brief after template sync
	try:
		from .runs import load_state

		state = load_state(project_id)
		if state.design_brief:
			write_brief(project_id, state.design_brief)
			apply_brief_css_tokens(app_dir, state.design_brief)
	except Exception:
		pass
	return app_dir


def _npm_install(app_dir: Path) -> None:
	subprocess.run(["npm", "install", "--silent"], cwd=app_dir, check=True)


def stop_preview(state: ProjectState) -> None:
	if state.preview_pid:
		try:
			os.kill(state.preview_pid, signal.SIGTERM)
		except ProcessLookupError:
			pass
		state.preview_pid = None
		state.preview_port = None


def start_preview(state: ProjectState, data_rows: list[dict], *, app_dir: Path | None = None) -> str:
	stop_preview(state)
	if app_dir is None:
		app_dir = sync_app(state.id, state.app_config, data_rows)
	_npm_install(app_dir)
	subprocess.run(["npm", "run", "build", "--silent"], cwd=app_dir, check=True)
	port = _free_port()
	proc = subprocess.Popen(
		["npm", "run", "preview", "--", "--host", "127.0.0.1", "--port", str(port)],
		cwd=app_dir,
		stdout=subprocess.DEVNULL,
		stderr=subprocess.DEVNULL,
	)
	time.sleep(1.5)
	if proc.poll() is not None:
		raise RuntimeError("App preview failed to start (run npm install in templates/internal-app)")
	state.preview_port = port
	state.preview_pid = proc.pid
	url = f"http://127.0.0.1:{port}"
	(root := project_dir(state.id) / "audit" / "deploy.json").parent.mkdir(parents=True, exist_ok=True)
	root.write_text(
		json.dumps(
			{
				"preview_url": url,
				"pid": proc.pid,
				"port": port,
				"deployed": state.deployed,
			},
			indent=2,
		)
	)
	return url
