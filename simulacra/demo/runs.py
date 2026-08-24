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
class ChatThread:
	"""One conversation under a project (Cursor-style nested chat)."""

	id: str
	title: str
	created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	messages: list[ChatMessage] = field(default_factory=list)
	prompt: str = ""
	# None = share the project's artefact; set = this chat targets another format
	artifact_kind: str | None = None
	artifact_mode: str = "shared"  # shared | own


def _title_from_text(text: str, fallback: str = "New chat") -> str:
	line = (text or "").strip().split("\n")[0].strip()
	if not line:
		return fallback
	line = line[:56].rstrip()
	if len((text or "").strip()) > 56:
		line = f"{line}…"
	return line or fallback


def _new_chat_id() -> str:
	return f"chat_{uuid.uuid4().hex[:10]}"


def _thread_from_dict(raw: dict[str, Any]) -> ChatThread:
	msgs = [
		ChatMessage(**{k: v for k, v in m.items() if k in ("role", "content", "at", "source")})
		for m in (raw.get("messages") or [])
	]
	return ChatThread(
		id=str(raw.get("id") or _new_chat_id()),
		title=str(raw.get("title") or "Chat"),
		created_at=str(raw.get("created_at") or datetime.now(UTC).isoformat()),
		updated_at=str(raw.get("updated_at") or datetime.now(UTC).isoformat()),
		messages=msgs,
		prompt=str(raw.get("prompt") or ""),
		artifact_kind=raw.get("artifact_kind"),
		artifact_mode=str(raw.get("artifact_mode") or "shared"),
	)


def _migrate_chats(
	*,
	chat: list[ChatMessage],
	chats_raw: list[dict[str, Any]] | None,
	active_chat_id: str | None,
	prompt: str,
) -> tuple[list[ChatThread], str, list[ChatMessage]]:
	"""Ensure chats[] exists; legacy `chat` becomes the first thread."""
	chats = [_thread_from_dict(c) for c in (chats_raw or []) if isinstance(c, dict)]
	if not chats:
		seed = list(chat)
		title = _title_from_text(prompt or (seed[0].content if seed else ""), "Main chat")
		cid = _new_chat_id()
		chats = [
			ChatThread(
				id=cid,
				title=title,
				messages=seed,
				prompt=prompt or "",
			)
		]
		active = cid
	else:
		active = active_chat_id or chats[0].id
		if not any(t.id == active for t in chats):
			active = chats[0].id
	active_thread = next(t for t in chats if t.id == active)
	# Prefer thread messages; fall back to legacy chat if thread empty but legacy has content
	messages = list(active_thread.messages) if active_thread.messages else list(chat)
	active_thread.messages = messages
	return chats, active, messages


def sync_chat_threads(state: ProjectState) -> None:
	"""Keep `state.chat` and the active ChatThread.messages in lockstep."""
	if not state.chats:
		state.chats, state.active_chat_id, state.chat = _migrate_chats(
			chat=list(state.chat),
			chats_raw=None,
			active_chat_id=None,
			prompt=state.prompt,
		)
		return
	thread = next((t for t in state.chats if t.id == state.active_chat_id), None)
	if thread is None:
		thread = state.chats[0]
		state.active_chat_id = thread.id
	thread.messages = list(state.chat)
	thread.updated_at = datetime.now(UTC).isoformat()
	if (not thread.title or thread.title in ("New chat", "Chat", "Main chat")) and state.chat:
		first_user = next((m.content for m in state.chat if m.role == "user"), "")
		if first_user:
			thread.title = _title_from_text(first_user, thread.title or "Chat")


def get_active_thread(state: ProjectState) -> ChatThread:
	sync_chat_threads(state)
	thread = next((t for t in state.chats if t.id == state.active_chat_id), None)
	if thread is None:
		thread = state.chats[0]
		state.active_chat_id = thread.id
	return thread


def activate_chat(project_id: str, chat_id: str) -> ProjectState:
	"""Switch active thread. Stale client chat ids heal to the active/first thread."""
	state = load_state(project_id)
	sync_chat_threads(state)
	thread = next((t for t in state.chats if t.id == chat_id), None)
	if thread is None:
		# Old clients / remigrated projects may still send a dead chat id.
		thread = get_active_thread(state)
		save_state(state)
		return load_state(project_id)
	# Persist current messages into previous active thread first
	prev = next((t for t in state.chats if t.id == state.active_chat_id), None)
	if prev and prev.id != thread.id:
		prev.messages = list(state.chat)
		prev.updated_at = datetime.now(UTC).isoformat()
	state.active_chat_id = thread.id
	state.chat = list(thread.messages)
	# Own-artefact chats can steer the project's format when switched in
	if thread.artifact_mode == "own" and thread.artifact_kind:
		from .formats import normalize_kind

		state.artifact_kind = normalize_kind(thread.artifact_kind)
	save_state(state)
	return load_state(project_id)


def create_chat(
	project_id: str,
	*,
	title: str | None = None,
	prompt: str = "",
	artifact_kind: str | None = None,
	artifact_mode: str = "shared",
) -> ProjectState:
	"""Start a new conversation under the project (shares sources; artefact optional)."""
	from .formats import normalize_kind

	state = load_state(project_id)
	sync_chat_threads(state)
	# Flush active thread before switching
	cur = get_active_thread(state)
	cur.messages = list(state.chat)
	cur.updated_at = datetime.now(UTC).isoformat()

	mode = "own" if artifact_mode == "own" or artifact_kind else "shared"
	kind = normalize_kind(artifact_kind) if artifact_kind else None
	cid = _new_chat_id()
	seed: list[ChatMessage] = []
	if prompt.strip():
		seed.append(ChatMessage(role="user", content=prompt.strip(), source="system"))
	thread = ChatThread(
		id=cid,
		title=_title_from_text(title or prompt, "Chat"),
		messages=seed,
		prompt=prompt.strip(),
		artifact_kind=kind,
		artifact_mode=mode,
	)
	state.chats.insert(0, thread)
	state.active_chat_id = cid
	state.chat = list(seed)
	if mode == "own" and kind:
		state.artifact_kind = kind
	save_state(state)
	return load_state(project_id)


def delete_chat(project_id: str, chat_id: str) -> ProjectState:
	"""Remove a chat thread. Keeps at least one chat under the project."""
	state = load_state(project_id)
	if not state.chats:
		state.chats, state.active_chat_id, state.chat = _migrate_chats(
			chat=list(state.chat),
			chats_raw=None,
			active_chat_id=None,
			prompt=state.prompt,
		)
	# Flush active messages into their thread without touching others
	active = next((t for t in state.chats if t.id == state.active_chat_id), None)
	if active is None and state.chats:
		active = state.chats[0]
		state.active_chat_id = active.id
	if active:
		active.messages = list(state.chat)
		active.updated_at = datetime.now(UTC).isoformat()

	if len(state.chats) <= 1:
		raise ValueError("Cannot delete the only chat in this project")
	if not any(t.id == chat_id for t in state.chats):
		save_state(state)
		return load_state(project_id)

	was_active = state.active_chat_id == chat_id
	state.chats = [t for t in state.chats if t.id != chat_id]
	if was_active:
		nxt = state.chats[0]
		state.active_chat_id = nxt.id
		state.chat = list(nxt.messages)
	save_state(state)
	return load_state(project_id)

def chat_summaries(state: ProjectState) -> list[dict[str, Any]]:
	sync_chat_threads(state)
	out: list[dict[str, Any]] = []
	for t in sorted(state.chats, key=lambda x: x.updated_at, reverse=True):
		out.append(
			{
				"id": t.id,
				"title": t.title,
				"updated_at": t.updated_at,
				"created_at": t.created_at,
				"message_count": len(t.messages),
				"artifact_kind": t.artifact_kind,
				"artifact_mode": t.artifact_mode,
				"active": t.id == state.active_chat_id,
			}
		)
	return out


@dataclass
class AppConfig:
	title: str = "Untitled"
	subtitle: str = "From your sources"
	search_enabled: bool = True
	sort_column: str = ""
	sort_direction: str = "desc"
	group_by: str | None = None
	highlight_column: str = ""
	columns: list[str] = field(default_factory=list)


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
	# data_app | report | slides | one_pager — same maker loop, different craft
	artifact_kind: str = "data_app"
	created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	preview_port: int | None = None
	preview_pid: int | None = None
	deployed: bool = False
	deploy_url: str | None = None
	gates_status: str = "pending"
	chat: list[ChatMessage] = field(default_factory=list)
	# Project → many chats (Cursor-style). `chat` mirrors the active thread.
	active_chat_id: str = ""
	chats: list[ChatThread] = field(default_factory=list)
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
		from .formats import normalize_kind

		chats, active_id, chat = _migrate_chats(
			chat=chat,
			chats_raw=data.get("chats"),
			active_chat_id=data.get("active_chat_id"),
			prompt=str(data.get("prompt") or ""),
		)

		return cls(
			id=data["id"],
			prompt=data["prompt"],
			goal=data.get("goal", ""),
			tenant_id=data.get("tenant_id") or default_tenant_id(),
			phase=data.get("phase", "plan"),
			plan_approved=data.get("plan_approved", False),
			status=data.get("status", "planning"),
			artifact_kind=normalize_kind(data.get("artifact_kind")),
			created_at=data.get("created_at", datetime.now(UTC).isoformat()),
			updated_at=data.get("updated_at", datetime.now(UTC).isoformat()),
			preview_port=data.get("preview_port"),
			preview_pid=data.get("preview_pid"),
			deployed=data.get("deployed", False),
			deploy_url=data.get("deploy_url"),
			gates_status=data.get("gates_status", "pending"),
			chat=chat,
			active_chat_id=active_id,
			chats=chats,
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
	path = state_path(project_id)
	raw = path.read_text().strip() if path.exists() else ""
	if not raw:
		raise FileNotFoundError(f"Empty or missing state for {project_id}")
	try:
		return ProjectState.from_dict(json.loads(raw))
	except json.JSONDecodeError as exc:
		raise ValueError(f"Corrupt state.json for {project_id}: {exc}") from exc


def save_state(state: ProjectState) -> None:
	"""Atomic write so concurrent readers never see empty/partial JSON."""
	sync_chat_threads(state)
	state.updated_at = datetime.now(UTC).isoformat()
	path = state_path(state.id)
	path.parent.mkdir(parents=True, exist_ok=True)
	payload = json.dumps(state.to_dict(), indent=2)
	tmp = path.with_suffix(".json.tmp")
	tmp.write_text(payload)
	tmp.replace(path)


def create_project(
	prompt: str,
	*,
	use_fixture: bool = False,
	goal: str = "",
	design_brief: dict[str, Any] | None = None,
	tenant_id: str | None = None,
	artifact_kind: str | None = None,
) -> ProjectState:
	from .formats import brief_defaults_for, infer_kind_from_prompt, normalize_kind
	from .tenants import assert_under_project_quota

	ensure_runs_dir()
	tid = tenant_id or default_tenant_id()
	assert_under_project_quota(tid)

	project_id = f"proj_{uuid.uuid4().hex[:12]}"
	root = project_dir(project_id)
	for sub in ("inputs/data-room", "outputs", "work", "app", "audit"):
		(root / sub).mkdir(parents=True, exist_ok=True)

	if use_fixture and FIXTURES.exists():
		shutil.copytree(FIXTURES, root / "inputs/data-room", dirs_exist_ok=True)

	kind = normalize_kind(artifact_kind) if artifact_kind else (infer_kind_from_prompt(prompt) or "data_app")
	kind = normalize_kind(kind)
	# Prompt can outrank a default App selection when it clearly asks for another format
	inferred = infer_kind_from_prompt(prompt)
	if inferred and inferred != kind and kind == "data_app":
		kind = inferred
	brief = design_brief if design_brief else default_brief(prompt=prompt, artifact_kind=kind)
	if design_brief:
		# Ensure IA knows the chosen format even when a custom brief is passed
		from .design_brief import merge_brief

		brief = merge_brief(brief, brief_defaults_for(kind))
	from .design_brief import is_stock_vendor_name, title_from_prompt

	product = str(brief.get("product_name") or "").strip()
	if not product or is_stock_vendor_name(product):
		product = title_from_prompt(prompt)
		brief["product_name"] = product
		brief["one_liner"] = str(brief.get("one_liner") or f"{product} — research brief")
	state = ProjectState(
		id=project_id,
		prompt=prompt,
		goal=goal,
		tenant_id=tid,
		phase="plan",
		status="planning",
		artifact_kind=kind,
		design_brief=brief,
		app_config=AppConfig(
			title=product[:80],
			subtitle=str(brief.get("one_liner") or "From your sources")[:120],
		),
	)
	first = ChatMessage(role="user", content=prompt, source="system")
	cid = _new_chat_id()
	state.active_chat_id = cid
	state.chats = [
		ChatThread(
			id=cid,
			title=_title_from_text(prompt, "Main chat"),
			messages=[first],
			prompt=prompt,
			artifact_kind=kind,
			artifact_mode="shared",
		)
	]
	state.chat = [first]
	write_brief(project_id, brief)
	save_state(state)

	(root / "simulacra.yaml").write_text(
		yaml.safe_dump(
			{
				"run": {"id": project_id, "task": prompt, "tenant_id": tid},
				"artifact_kind": kind,
				"sources": [{"type": "folder", "path": "inputs/data-room"}],
				"design_brief": brief,
			}
		)
	)
	return state


def list_projects(*, tenant_id: str | None = None) -> list[ProjectState]:
	ensure_runs_dir()
	out: list[ProjectState] = []
	try:
		entries = list(RUNS_DIR.iterdir())
	except OSError:
		return out

	def modified_at(path: Path) -> float:
		try:
			return path.stat(follow_symlinks=False).st_mtime
		except OSError:
			return -1.0

	for path in sorted(entries, key=modified_at, reverse=True):
		try:
			# Persistent volumes may contain filesystem-owned metadata such as
			# ext4's lost+found. It is not application state and is commonly not
			# traversable by the unprivileged API user.
			if path.name == "lost+found" or path.name.startswith("."):
				continue
			if path.is_symlink() or not path.is_dir():
				continue
			candidate = path / "state.json"
			if candidate.is_symlink() or not candidate.is_file():
				continue
			state = load_state(path.name)
		except Exception:  # noqa: BLE001 — one unsafe/corrupt entry must not empty the list
			continue
		if tenant_id and state.tenant_id != tenant_id:
			continue
		out.append(state)
	return out


def file_hash(path: Path) -> str:
	h = hashlib.sha256()
	h.update(path.read_bytes())
	return f"sha256:{h.hexdigest()}"
