"""Checkpoint snapshots for rollback."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runs import AppConfig, ChatMessage, ProjectState, load_state, project_dir, save_state


def _checkpoints_dir(project_id: str) -> Path:
	path = project_dir(project_id) / "audit" / "checkpoints"
	path.mkdir(parents=True, exist_ok=True)
	return path


def save_checkpoint(state: ProjectState, label: str) -> dict[str, Any]:
	ck_id = f"ck_{len(state.checkpoints) + 1:03d}"
	created = datetime.now(UTC).isoformat()
	snapshot: dict[str, Any] = {
		"id": ck_id,
		"label": label,
		"created_at": created,
		"prompt": state.prompt,
		"goal": state.goal,
		"phase": state.phase,
		"plan_approved": state.plan_approved,
		"app_config": asdict(state.app_config),
		"row_count": state.row_count,
		"status": state.status,
	}
	app_config_path = project_dir(state.id) / "app" / "public" / "config.json"
	if app_config_path.exists():
		snapshot["config_json"] = json.loads(app_config_path.read_text())

	(_checkpoints_dir(state.id) / f"{ck_id}.json").write_text(json.dumps(snapshot, indent=2))
	entry = {"id": ck_id, "label": label, "created_at": created}
	state.checkpoints.append(entry)
	state.active_checkpoint = len(state.checkpoints) - 1
	save_state(state)
	return entry


def list_checkpoints(project_id: str) -> list[dict[str, Any]]:
	state = load_state(project_id)
	return list(state.checkpoints)


def rollback(project_id: str, checkpoint_id: str | None = None) -> ProjectState:
	state = load_state(project_id)
	if not state.checkpoints:
		raise ValueError("No checkpoints to roll back to")

	if checkpoint_id is None:
		idx = max(0, state.active_checkpoint - 1)
		checkpoint_id = state.checkpoints[idx]["id"]
	else:
		idx = next((i for i, c in enumerate(state.checkpoints) if c["id"] == checkpoint_id), -1)
		if idx < 0:
			raise ValueError(f"Checkpoint {checkpoint_id} not found")

	path = _checkpoints_dir(project_id) / f"{checkpoint_id}.json"
	if not path.exists():
		raise ValueError(f"Checkpoint file missing: {checkpoint_id}")

	snap = json.loads(path.read_text())
	state.prompt = snap.get("prompt", state.prompt)
	state.goal = snap.get("goal", state.goal)
	state.phase = snap.get("phase", state.phase)
	state.plan_approved = snap.get("plan_approved", state.plan_approved)
	state.app_config = AppConfig(**(snap.get("app_config") or {}))
	state.row_count = snap.get("row_count", state.row_count)
	state.active_checkpoint = idx
	state.status = snap.get("status", "ready")

	# Plain language — users don't care about checkpoint IDs/labels
	msg = "Undid — preview restored."
	# Don't spam identical undo lines if they hit Undo twice
	last = state.chat[-1] if state.chat else None
	if not (last and last.role == "assistant" and (last.content or "").startswith("Undid")):
		state.chat.append(
			ChatMessage(
				role="assistant",
				content=msg,
				source="system",
			)
		)

	if "config_json" in snap:
		config_path = project_dir(project_id) / "app" / "public" / "config.json"
		config_path.parent.mkdir(parents=True, exist_ok=True)
		config_path.write_text(json.dumps(snap["config_json"], indent=2))

	save_state(state)
	return state
