"""Checkpoint snapshots for version restore (no forks)."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runs import AppConfig, ChatMessage, ProjectState, load_state, project_dir, save_state

# Keep enough history for a Versions menu without filling the disk.
MAX_VERSIONS = 12
_APP_IGNORE = shutil.ignore_patterns(
	"node_modules",
	".git",
	"*.log",
	".DS_Store",
	"dist/.vite",
)


def _checkpoints_dir(project_id: str) -> Path:
	path = project_dir(project_id) / "audit" / "checkpoints"
	path.mkdir(parents=True, exist_ok=True)
	return path


def version_label(message: str, *, fallback: str = "Update") -> str:
	"""Human version name from a chat instruction — e.g. 'Added chart', 'Improved car'."""
	text = " ".join((message or "").strip().split())
	if not text:
		return fallback
	# Drop chat fluff so the menu stays scannable
	lower = text.lower()
	for prefix in (
		"please ",
		"can you ",
		"could you ",
		"would you ",
		"hey ",
		"hi ",
		"make it ",
		"make the ",
		"make a ",
		"i want ",
		"i'd like ",
		"id like ",
	):
		if lower.startswith(prefix):
			text = text[len(prefix) :]
			lower = text.lower()
			break
	# Prefer a short verb phrase
	words = text.split()
	clipped = " ".join(words[:7])
	if len(words) > 7:
		clipped += "…"
	# Soft title: capitalize first letter only (keeps "BJP", "API")
	label = clipped[:1].upper() + clipped[1:] if clipped else fallback
	return label[:72]


def friendly_version_label(raw: str | None) -> str | None:
	"""Map legacy / internal labels for the Versions menu. Returns None to hide."""
	if not raw:
		return None
	label = str(raw).strip()
	if not label:
		return None
	# Pre-iterate safety snaps — not user-facing versions
	if re.match(r"^Before:\s*", label, re.I):
		return None
	m = re.match(r"^After:\s*(.+)$", label, re.I)
	if m:
		return version_label(m.group(1))
	if label in ("Built", "Build", "Initial build"):
		return "First build"
	return label


def _copy_app_snapshot(project_id: str, ck_id: str) -> bool:
	"""Copy preview-relevant app files into the checkpoint folder."""
	app = project_dir(project_id) / "app"
	if not app.is_dir():
		return False
	dest = _checkpoints_dir(project_id) / ck_id / "app"
	if dest.exists():
		shutil.rmtree(dest, ignore_errors=True)
	# Prefer lean copies: src + public + dist (served preview)
	dest.mkdir(parents=True, exist_ok=True)
	copied = False
	for name in ("src", "public", "dist", "index.html"):
		src = app / name
		if not src.exists():
			continue
		target = dest / name
		if src.is_dir():
			shutil.copytree(src, target, ignore=_APP_IGNORE, dirs_exist_ok=True)
		else:
			shutil.copy2(src, target)
		copied = True
	# Always try config if public copy missed it
	cfg = app / "public" / "config.json"
	if cfg.exists():
		(dest / "public").mkdir(parents=True, exist_ok=True)
		shutil.copy2(cfg, dest / "public" / "config.json")
		copied = True
	return copied


def _restore_app_snapshot(project_id: str, ck_id: str, snap: dict[str, Any]) -> None:
	app = project_dir(project_id) / "app"
	bundle = _checkpoints_dir(project_id) / ck_id / "app"
	if bundle.is_dir():
		for name in ("src", "public", "dist", "index.html"):
			src = bundle / name
			if not src.exists():
				continue
			target = app / name
			if src.is_dir():
				if target.exists():
					shutil.rmtree(target, ignore_errors=True)
				shutil.copytree(src, target, ignore=_APP_IGNORE)
			else:
				target.parent.mkdir(parents=True, exist_ok=True)
				shutil.copy2(src, target)
		return
	# Legacy checkpoints: config_json only
	if "config_json" in snap:
		config_path = app / "public" / "config.json"
		config_path.parent.mkdir(parents=True, exist_ok=True)
		config_path.write_text(json.dumps(snap["config_json"], indent=2))


def _prune_old_versions(state: ProjectState) -> None:
	"""Drop oldest versions beyond MAX_VERSIONS (files + index)."""
	while len(state.checkpoints) > MAX_VERSIONS:
		old = state.checkpoints.pop(0)
		ck_id = old.get("id")
		if ck_id:
			folder = _checkpoints_dir(state.id) / ck_id
			if folder.exists():
				shutil.rmtree(folder, ignore_errors=True)
			legacy = _checkpoints_dir(state.id) / f"{ck_id}.json"
			if legacy.exists():
				legacy.unlink(missing_ok=True)
		if state.active_checkpoint > 0:
			state.active_checkpoint -= 1


def save_checkpoint(state: ProjectState, label: str) -> dict[str, Any]:
	ck_id = f"ck_{uuid.uuid4().hex[:10]}"
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
	has_files = _copy_app_snapshot(state.id, ck_id)
	entry = {
		"id": ck_id,
		"label": label,
		"created_at": created,
		"has_files": str(has_files),
	}
	state.checkpoints.append(entry)
	_prune_old_versions(state)
	state.active_checkpoint = len(state.checkpoints) - 1
	save_state(state)
	return entry


def list_checkpoints(project_id: str) -> list[dict[str, Any]]:
	"""Versions for the UI — descriptive labels only, newest first."""
	state = load_state(project_id)
	out: list[dict[str, Any]] = []
	for i, c in enumerate(state.checkpoints):
		friendly = friendly_version_label(c.get("label"))
		if not friendly:
			continue
		out.append(
			{
				**c,
				"label": friendly,
				"raw_label": c.get("label"),
				"current": i == state.active_checkpoint,
				"index": i,
			}
		)
	out.reverse()
	return out


def rollback(project_id: str, checkpoint_id: str | None = None) -> ProjectState:
	state = load_state(project_id)
	if not state.checkpoints:
		raise ValueError("No versions to restore")

	if checkpoint_id is None:
		idx = max(0, state.active_checkpoint - 1)
		checkpoint_id = state.checkpoints[idx]["id"]
	else:
		idx = next((i for i, c in enumerate(state.checkpoints) if c["id"] == checkpoint_id), -1)
		if idx < 0:
			raise ValueError(f"Version {checkpoint_id} not found")

	path = _checkpoints_dir(project_id) / f"{checkpoint_id}.json"
	if not path.exists():
		raise ValueError(f"Version file missing: {checkpoint_id}")

	snap = json.loads(path.read_text())
	state.prompt = snap.get("prompt", state.prompt)
	state.goal = snap.get("goal", state.goal)
	state.phase = snap.get("phase", state.phase)
	state.plan_approved = snap.get("plan_approved", state.plan_approved)
	state.app_config = AppConfig(**(snap.get("app_config") or {}))
	state.row_count = snap.get("row_count", state.row_count)
	state.active_checkpoint = idx
	state.status = snap.get("status", "ready")

	_restore_app_snapshot(project_id, checkpoint_id, snap)

	label = friendly_version_label(snap.get("label")) or "earlier version"
	msg = f"Restored “{label}”."
	last = state.chat[-1] if state.chat else None
	if not (last and last.role == "assistant" and (last.content or "").startswith("Restored")):
		state.chat.append(
			ChatMessage(
				role="assistant",
				content=msg,
				source="system",
			)
		)

	save_state(state)
	return state
