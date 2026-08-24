"""Product integration for the provider-neutral CMUL8 builder harness."""

from __future__ import annotations

import asyncio
import json
import hashlib
import os
import secrets
import stat
from pathlib import Path
from typing import Any

from simulacra.harnesses import (
	AgentRunRequest,
	HarnessConfig,
	NetworkPolicy,
	TaskType,
	TerminalStatus,
	create_harness,
)
from simulacra.operation_graph import (
	OperationGraphStore,
	canonical_json_bytes,
	validate_operation_graph,
)
from simulacra.operation_graph.errors import OperationGraphError
from simulacra.operation_graph.security import assert_connector_configurations_opaque


def _verified_approved_graph(
	path: Path, *, workspace: Path, project_id: str,
) -> tuple[bytes, str]:
	resolved = path.resolve()
	root = workspace.resolve()
	if resolved != root and root not in resolved.parents:
		raise PermissionError("Approved Operation Graph artifact escapes the project workspace")
	try:
		supplied = validate_operation_graph(json.loads(resolved.read_text(encoding="utf-8")))
		assert_connector_configurations_opaque(supplied)
	except (OSError, json.JSONDecodeError, ValueError, OperationGraphError) as exc:
		raise PermissionError("Approved Operation Graph artifact is invalid") from exc
	metadata = supplied["metadata"]
	if metadata["project_id"] != project_id:
		raise PermissionError("Approved Operation Graph artifact does not match the project")
	store = OperationGraphStore(root, tenant_id=metadata["tenant_id"], project_id=project_id)
	try:
		current = store.current_revision()
		if current is None:
			raise PermissionError("No immutable Operation Graph revision exists for this project")
		approved = store.require_approved_revision(current.revision_hash)
		assert_connector_configurations_opaque(approved.graph)
	except PermissionError:
		raise
	except (OSError, ValueError, OperationGraphError) as exc:
		raise PermissionError("No safe approved Operation Graph revision exists for this project") from exc
	supplied_hash = hashlib.sha256(canonical_json_bytes(supplied)).hexdigest()
	if supplied_hash != approved.revision_hash or supplied != approved.graph:
		raise PermissionError("Approved Operation Graph artifact was changed or is not the current exact revision")
	# These are the canonical bytes whose digest is the immutable revision hash.
	# Never reserialize caller-controlled bytes into the builder-visible asset.
	return canonical_json_bytes(approved.graph), approved.revision_hash


def _safe_graph_asset_path(app_dir: Path) -> Path:
	"""Return the builder-visible path after no-follow publication has checked it."""
	return app_dir / "public" / "operation-graph.json"


def _open_directory(path: Path, *, label: str, dir_fd: int | None = None) -> int:
	flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
	no_follow = getattr(os, "O_NOFOLLOW", None)
	if no_follow is None:
		raise PermissionError("safe approved Operation Graph publication requires no-follow filesystem support")
	try:
		fd = os.open(path if dir_fd is None else path.name, flags | no_follow, dir_fd=dir_fd)
		expected = os.fstat(fd)
		if not stat.S_ISDIR(expected.st_mode):
			os.close(fd)
			raise PermissionError(f"{label} is not a directory")
		return fd
	except OSError as exc:
		raise PermissionError(f"{label} may not be a symlink") from exc


def _publish_approved_graph_asset(app_dir: Path, content: bytes) -> Path:
	"""Atomically publish canonical graph bytes without traversing app children.

	Directory descriptors pin the app/public directories during the write.  A
	file symlink is atomically replaced as an entry; a symlinked public directory
	is rejected before any outside location can be opened or modified.
	"""
	app_fd = _open_directory(app_dir, label="application directory")
	public_fd: int | None = None
	temporary: str | None = None
	try:
		try:
			os.mkdir("public", mode=0o755, dir_fd=app_fd)
		except FileExistsError:
			pass
		public_fd = _open_directory(Path("public"), label="application public directory", dir_fd=app_fd)
		for _ in range(16):
			candidate = f".operation-graph.{secrets.token_hex(16)}.tmp"
			try:
				fd = os.open(candidate, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=public_fd)
				temporary = candidate
				break
			except FileExistsError:
				continue
		else:
			raise PermissionError("could not safely allocate approved Operation Graph asset")
		try:
			with os.fdopen(fd, "wb") as handle:
				handle.write(content)
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(temporary, "operation-graph.json", src_dir_fd=public_fd, dst_dir_fd=public_fd)
			temporary = None
			os.fsync(public_fd)
		except OSError as exc:
			raise PermissionError("could not safely publish the approved Operation Graph asset") from exc
	finally:
		if temporary is not None and public_fd is not None:
			try:
				os.unlink(temporary, dir_fd=public_fd)
			except OSError:
				pass
		if public_fd is not None:
			os.close(public_fd)
		os.close(app_fd)
	return _safe_graph_asset_path(app_dir)


def _read_published_graph_asset(app_dir: Path) -> bytes:
	app_fd = _open_directory(app_dir, label="application directory")
	public_fd: int | None = None
	try:
		public_fd = _open_directory(Path("public"), label="application public directory", dir_fd=app_fd)
		try:
			fd = os.open("operation-graph.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=public_fd)
			with os.fdopen(fd, "rb") as handle:
				mode = os.fstat(handle.fileno()).st_mode
				if not stat.S_ISREG(mode):
					raise PermissionError("approved Operation Graph asset is not a regular file")
				return handle.read()
		except OSError as exc:
			raise PermissionError("approved Operation Graph asset may not be a symlink") from exc
	finally:
		if public_fd is not None:
			os.close(public_fd)
		os.close(app_fd)


def _prime_runner(app_dir: Path, project_id: str, row_count: int, kind: str):
	def run(*, request: AgentRunRequest, session: object) -> dict[str, Any]:
		del session
		from .prime_builder import prime_build_app

		meta = prime_build_app(
			app_dir,
			request.prompt,
			project_id=project_id,
			row_count=row_count,
			kind=kind,
		)
		changed = []
		for item in meta.get("changed_files") or ():
			path = Path(item)
			if not path.is_absolute():
				path = app_dir / path
			changed.append(path)
		return {
			"status": TerminalStatus.SUCCEEDED if meta.get("ok") else TerminalStatus.FAILED,
			"response": None,
			"changed_files": changed,
			"events": [{"type": "prime_build", "status": "completed" if meta.get("ok") else "failed"}],
			"steps": int(meta.get("events") or 1),
			"error": meta.get("error"),
			"legacy_meta": meta,
		}

	return run


_CHAT_OUTPUT_SCHEMA = {
	"type": "object",
	"required": ["reply", "request"],
	"properties": {
		"title": {"type": ["string", "null"]},
		"subtitle": {"type": ["string", "null"]},
		"reply": {"type": "string"},
		"request": {"type": "string", "enum": ["await_user", "build", "iterate", "research"]},
		"brief": {"type": ["string", "null"]},
	},
	"additionalProperties": False,
}


def _chat_prompt(state: Any, *, message: str | None, open_turn: bool) -> str:
	from .design_brief import brief_to_prime_block
	from .prime_hook import _data_block, _envelope_schema_block, _recent_chat_block, _room_lines

	preview = state.plan_preview or {}
	has_artifact = state.phase in ("ready", "build") and bool(state.deploy_url)
	if open_turn:
		conversation = (
			"This is the opening turn after project creation. Do not claim that an artifact is already built.\n"
			f"User request:\n{state.prompt}\n\nGoal:\n{state.goal or '(none)'}\n\n"
			f"Data room inventory:\n{_room_lines(preview)}\n\n"
			f"Source summary:\n{str(preview.get('summary') or '')[:1800]}\n\n"
			f"{_data_block(preview)}\n\n{brief_to_prime_block(state.design_brief or {})}"
		)
	else:
		conversation = (
			"Continue the same product conversation; do not restart from scratch.\n"
			f"Original request: {state.prompt[:600]}\n\n"
			f"Recent chat:\n{_recent_chat_block(state)}\n\nLatest user message:\n{message or ''}\n\n"
			f"Data room inventory:\n{_room_lines(preview)}"
		)
	return (
		"You are the CMUL8 product architect in the main project chat. Be concise and honest about available "
		"sources. Do not write application source in a chat turn. Use request=build or request=iterate when "
		"the user is asking for an artifact change. "
		+ ("An artifact exists.\n\n" if has_artifact else "No artifact exists yet; never request iterate.\n\n")
		+ conversation
		+ "\n\n"
		+ _envelope_schema_block()
	)


def _prime_chat_runner(
	root: Path, state: Any, *, message: str | None, open_turn: bool, read_only: bool,
):
	def run(*, request: AgentRunRequest, session: object) -> dict[str, Any]:
		del request, session
		from .prime_hook import prime_chat_turn

		turn = prime_chat_turn(
			root, state, message=message, open_turn=open_turn, project_id=state.id,
			ephemeral_session=read_only,
		)
		structured = {
			"title": turn.title,
			"subtitle": turn.subtitle,
			"reply": turn.reply or "",
			"request": turn.request,
			"brief": turn.brief,
		}
		return {
			"status": TerminalStatus.SUCCEEDED if turn.reply and not turn.meta.error else TerminalStatus.FAILED,
			"response": json.dumps(structured),
			"structured_output": structured,
			"events": [{"type": "prime_chat", "status": "completed" if turn.reply else "failed"}],
			"steps": 1,
			"error": {"code": "prime_chat_error", "message": turn.meta.error} if turn.meta.error else None,
		}

	return run


def run_chat_builder(
	state: Any, *, message: str | None, open_turn: bool, read_only: bool = False,
):
	"""Run one persistent, provider-neutral main-chat turn.

	Read-only turns are deliberately unable to create a research workspace or
	write through the harness.  They may still return a conversational reply.
	"""
	from .prime_hook import PrimeBuildMeta, PrimeChatTurn, _parse_chat_envelope

	from .runs import project_dir

	root = project_dir(state.id)
	config = HarnessConfig.from_env()
	adapters: dict[str, Any] = {}
	if config.harness == "prime":
		adapters["prime_runner"] = _prime_chat_runner(
			root, state, message=message, open_turn=open_turn, read_only=read_only,
		)
	harness = create_harness(config, **adapters)
	research = root / "work" / "research"
	if not read_only:
		research.mkdir(parents=True, exist_ok=True)
	request = AgentRunRequest(
		project_id=state.id,
		environment_id="builder",
		workspace=root,
		prompt=_chat_prompt(state, message=message, open_turn=open_turn),
		role="product_chat",
		task_type=TaskType.CHAT,
		read_paths=(root / "inputs" / "data-room", root / "work"),
		write_paths=() if read_only else (research,),
		network_policy=NetworkPolicy.DENY,
		config=config,
		metadata={"output_schema": _CHAT_OUTPUT_SCHEMA},
		session_mode="ephemeral" if read_only else "durable",
	)
	result = asyncio.run(harness.run(request))
	turn = _parse_chat_envelope(result.response)
	if result.structured_output:
		turn = _parse_chat_envelope(json.dumps(dict(result.structured_output)))
	error = None
	if result.error:
		error = str(result.error.get("message") or result.error.get("code") or "builder chat failed")
	turn.meta = PrimeBuildMeta(
		used=True,
		session_id=result.session_id,
		model=result.model_id,
		error=error,
		source=result.harness if result.status is TerminalStatus.SUCCEEDED else "error",
	)
	if turn.request == "iterate" and state.phase not in ("ready", "build"):
		turn.request = "build"
	return turn


def run_app_builder(
	app_dir: Path,
	prompt: str,
	*,
	project_id: str,
	row_count: int,
	kind: str,
	operation_graph_path: Path | None = None,
) -> dict[str, Any]:
	"""Run the explicitly selected builder; never fall back between adapters."""
	if operation_graph_path is None:
		raise PermissionError("An exactly approved Operation Graph revision is required before building")
	workspace = app_dir.parent
	approved_graph_bytes, approved_revision = _verified_approved_graph(
		operation_graph_path, workspace=workspace, project_id=project_id,
	)
	graph_asset = _publish_approved_graph_asset(app_dir, approved_graph_bytes)
	config = HarnessConfig.from_env()
	adapters: dict[str, Any] = {}
	if config.harness == "prime":
		adapters["prime_runner"] = _prime_runner(app_dir, project_id, row_count, kind)
	harness = create_harness(config, **adapters)
	graph_instruction = ""
	read_paths: tuple[Path, ...] = (app_dir,)
	if operation_graph_path is not None:
		graph_instruction = (
			"\n\nThe approved Operation Graph is at "
			f"{graph_asset} with immutable revision {approved_revision}. Treat it as the executable product contract: implement its entities, "
			"views, workflow states, tasks, approvals, permissions, agents, and automations. Do not invent "
			"an industry template or change the graph."
		)
		read_paths = (app_dir, graph_asset)
	request = AgentRunRequest(
		project_id=project_id,
		environment_id="builder",
		workspace=workspace,
		prompt=(
			prompt
			+ graph_instruction
			+ "\n\nWork directly in this application workspace. Produce a complete working application, "
			"not a report or narration. Preserve the existing build toolchain and ensure src/App.tsx remains bootable."
		),
		role="application_builder",
		task_type=TaskType.ITERATE if kind == "iterate_run" else TaskType.BUILD_APP,
		read_paths=read_paths,
		write_paths=(app_dir,),
		network_policy=NetworkPolicy.DENY,
		config=config,
		metadata={
			"fake": {"changed_files": (str(app_dir.relative_to(workspace) / "fake-artifact.txt"),)}
			if config.harness == "fake" else {}
		},
	)
	# The harness may write anywhere under app_dir, including the graph asset.  It
	# is an input to the build, never builder-owned output: restore the immutable
	# store-verified bytes even when the harness fails or raises.
	try:
		result = asyncio.run(harness.run(request))
	finally:
		_publish_approved_graph_asset(app_dir, approved_graph_bytes)
	if result.status is TerminalStatus.SUCCEEDED:
		# A successful build must still be based on the current exact approved
		# revision.  This catches a changed supplied artifact or a changed graph
		# head while the builder was running rather than accepting stale output.
		current_graph_bytes, current_revision = _verified_approved_graph(
			operation_graph_path, workspace=workspace, project_id=project_id,
		)
		if current_revision != approved_revision or current_graph_bytes != approved_graph_bytes:
			raise PermissionError("Approved Operation Graph revision changed during builder execution")
		if _read_published_graph_asset(app_dir) != approved_graph_bytes:
			raise RuntimeError("Could not restore the approved Operation Graph asset after builder execution")
	changed = [str(path) for path in result.changed_files]
	meta = {
		"ok": result.status is TerminalStatus.SUCCEEDED,
		"used": True,
		"source": result.harness,
		"session_id": result.session_id,
		"model": result.model_id,
		"error": dict(result.error) if result.error else None,
		"events": len(result.events),
		"changed_files": changed,
		"files_changed": bool(changed),
		"style_only": bool(changed) and all(Path(path).name.endswith(".css") for path in changed),
		"layout_customized": any(Path(path).name in {"App.tsx", "App.jsx"} for path in changed),
	}
	return meta
