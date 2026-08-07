from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .design_brief import default_brief, write_brief
from .paths import FIXTURES, RUNS_DIR, ensure_runs_dir
from .tenants import default_tenant_id


@dataclass
class ChatMessage:
	role: str
	content: str
	at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	source: str | None = None  # prime | heuristic | system


@dataclass
class AppConfig:
	title: str = "Data App"
	subtitle: str = "Built with Simulacra"
	search_enabled: bool = True
	sort_column: str = "risk_score"
	sort_direction: str = "desc"
	group_by: str | None = None
	highlight_column: str = "risk_level"
	columns: list[str] = field(
		default_factory=lambda: [
			"vendor",
			"theme",
			"risk_level",
			"risk_score",
			"region",
			"owner",
			"evidence",
		]
	)


def _empty_prime() -> dict[str, Any]:
	return {
		"session_id": None,
		"session_dir": None,
		"model": None,
		"source": "none",
		"last_error": None,
		"status": "idle",
		"steps": 0,
		"duration_ms": 0,
	}


def _empty_job() -> dict[str, Any]:
	return {
		"id": None,
		"kind": None,
		"status": "idle",  # idle | running | settling | failed | cancelled
		"started_at": None,
		"deadline_at": None,
		"steps": 0,
		"max_steps": 0,
		"cancel_requested": False,
		"error": None,
		"label": None,
	}


@dataclass
class ProjectState:
	id: str
	prompt: str
	goal: str = ""
	tenant_id: str = field(default_factory=default_tenant_id)
	phase: str = "plan"  # plan | build | ready
	plan_approved: bool = False
	status: str = "planning"
	created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	preview_port: int | None = None
	preview_pid: int | None = None
	deployed: bool = False
	deploy_url: str | None = None
	gates_status: str = "pending"
	chat: list[ChatMessage] = field(default_factory=list)
	app_config: AppConfig = field(default_factory=AppConfig)
	row_count: int = 0
	checkpoints: list[dict[str, str]] = field(default_factory=list)
	active_checkpoint: int = -1
	plan_preview: dict[str, Any] = field(default_factory=dict)
	design_brief: dict[str, Any] = field(default_factory=dict)
	prime: dict[str, Any] = field(default_factory=_empty_prime)
	job: dict[str, Any] = field(default_factory=_empty_job)
	sandbox: dict[str, Any] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> ProjectState:
		chat = [ChatMessage(**{k: v for k, v in m.items() if k in ("role", "content", "at", "source")}) for m in data.get("chat", [])]
		cfg = data.get("app_config") or {}
		return cls(
			id=data["id"],
			prompt=data["prompt"],
			goal=data.get("goal", ""),
			tenant_id=data.get("tenant_id") or default_tenant_id(),
			phase=data.get("phase", "plan"),
			plan_approved=data.get("plan_approved", False),
			status=data.get("status", "planning"),
			created_at=data.get("created_at", datetime.now(UTC).isoformat()),
			updated_at=data.get("updated_at", datetime.now(UTC).isoformat()),
			preview_port=data.get("preview_port"),
			preview_pid=data.get("preview_pid"),
			deployed=data.get("deployed", False),
			deploy_url=data.get("deploy_url"),
			gates_status=data.get("gates_status", "pending"),
			chat=chat,
			app_config=AppConfig(**{k: v for k, v in cfg.items() if k in AppConfig.__dataclass_fields__}),
			row_count=data.get("row_count", 0),
			checkpoints=data.get("checkpoints", []),
			active_checkpoint=data.get("active_checkpoint", -1),
			plan_preview=data.get("plan_preview", {}),
			design_brief=data.get("design_brief") or default_brief(prompt=data.get("prompt", "")),
			prime={**_empty_prime(), **(data.get("prime") or {})},
			job={**_empty_job(), **(data.get("job") or {})},
			sandbox=data.get("sandbox") or {},
		)


def project_dir(project_id: str) -> Path:
	return RUNS_DIR / project_id


def state_path(project_id: str) -> Path:
	return project_dir(project_id) / "state.json"


def load_state(project_id: str) -> ProjectState:
	return ProjectState.from_dict(json.loads(state_path(project_id).read_text()))


def save_state(state: ProjectState) -> None:
	state.updated_at = datetime.now(UTC).isoformat()
	state_path(state.id).write_text(json.dumps(state.to_dict(), indent=2))


def create_project(
	prompt: str,
	*,
	use_fixture: bool = True,
	goal: str = "",
	design_brief: dict[str, Any] | None = None,
	tenant_id: str | None = None,
) -> ProjectState:
	from .tenants import assert_tenant_active

	ensure_runs_dir()
	tid = tenant_id or default_tenant_id()
	assert_tenant_active(tid)

	project_id = f"proj_{uuid.uuid4().hex[:12]}"
	root = project_dir(project_id)
	for sub in ("inputs/data-room", "outputs", "work", "app", "audit"):
		(root / sub).mkdir(parents=True, exist_ok=True)

	if use_fixture and FIXTURES.exists():
		shutil.copytree(FIXTURES, root / "inputs/data-room", dirs_exist_ok=True)

	brief = design_brief if design_brief else default_brief(prompt=prompt)
	state = ProjectState(
		id=project_id,
		prompt=prompt,
		goal=goal,
		tenant_id=tid,
		phase="plan",
		status="planning",
		design_brief=brief,
	)
	state.chat.append(ChatMessage(role="user", content=prompt, source="system"))
	write_brief(project_id, brief)
	save_state(state)

	(root / "simulacra.yaml").write_text(
		yaml.safe_dump(
			{
				"run": {"id": project_id, "task": prompt, "tenant_id": tid},
				"sources": [{"type": "folder", "path": "inputs/data-room"}],
				"design_brief": brief,
			}
		)
	)
	return state


def list_projects(*, tenant_id: str | None = None) -> list[ProjectState]:
	ensure_runs_dir()
	out: list[ProjectState] = []
	for path in sorted(RUNS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
		if path.is_dir() and (path / "state.json").exists():
			state = load_state(path.name)
			if tenant_id and state.tenant_id != tenant_id:
				continue
			out.append(state)
	return out


def file_hash(path: Path) -> str:
	h = hashlib.sha256()
	h.update(path.read_bytes())
	return f"sha256:{h.hexdigest()}"
