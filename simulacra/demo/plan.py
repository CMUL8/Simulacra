"""Create scan + Prime chat entry. Build is a separate user action."""

from __future__ import annotations

import json
from typing import Any

from .chat import infer_app_config
from .design_brief import merge_notes_from_message, write_brief
from .extract import extract_data_room_report, write_summary
from .events import emit_event
from .jobs import JobConflictError, start_job
from .prime_hook import prime_chat_turn
from .runs import ChatMessage, ProjectState, load_state, project_dir, save_state
from .sources import (
	apply_profile_to_brief,
	content_fingerprint,
	list_source_files,
	profile_rows,
	source_room_brief,
	write_agent_context,
)


def explore_plan_scan(state: ProjectState) -> ProjectState:
	"""Read-only extract for plan preview — does not talk to the user yet."""
	root = project_dir(state.id)
	pid = state.id
	data_room = root / "inputs" / "data-room"
	emit_event(pid, "phase", label="Scanning sources", status="running")
	report = extract_data_room_report(data_room, project_id=pid)
	rows = report.rows
	summary = write_summary(rows, state.prompt)
	profile = profile_rows(rows)
	sources = list_source_files(pid)

	files: list[dict[str, Any]] = [
		{
			"name": s.name,
			"size": s.size,
			"type": s.type,
			"status": s.status,
			"detail": s.detail,
			"sha256": s.sha256[:16],
		}
		for s in sources
	]

	state.plan_preview = {
		"row_count": len(rows),
		"high_risk": profile.high_risk,
		"medium_risk": profile.medium_risk,
		"low_risk": profile.low_risk,
		"vendors": profile.vendors,
		"themes": profile.themes,
		"files": files,
		"summary": summary,
		"sample_rows": rows[:5],
		"profile": profile.to_dict(),
		"extract": report.to_dict(),
		"fingerprint": content_fingerprint(pid),
		"source_room": None,
	}
	state.row_count = len(rows)
	state.status = "planning"
	state.prime = {**state.prime, "status": "starting", "source": "pending"}
	state.design_brief = apply_profile_to_brief(state.design_brief or {}, profile)
	write_brief(pid, state.design_brief)
	write_agent_context(
		pid, rows=rows, profile=profile, extract=report, prompt=state.prompt
	)
	state.plan_preview["source_room"] = source_room_brief(state.plan_preview)
	(root / "work" / "plan_preview.json").write_text(json.dumps(state.plan_preview, indent=2, default=str))
	detail = f"{len(rows)} rows · {len(files)} sources"
	if report.errors:
		detail += f" · {len(report.errors)} extract errors"
	emit_event(pid, "phase", label="Scanning sources", detail=detail, status="done")
	save_state(state)
	return state


def run_plan_open(project_id: str) -> ProjectState:
	"""Opening Prime turn after create."""
	return _agent_chat_turn(project_id, message=None, open_turn=True)


def init_plan(state: ProjectState) -> ProjectState:
	"""Scan sources, then open Prime in the main chat. User steers; Build scaffolds."""
	if not any(m.role == "user" for m in state.chat):
		state.chat.append(ChatMessage(role="user", content=state.prompt, source="system"))
		save_state(state)
	state = explore_plan_scan(state)
	pid = state.id

	state.app_config = infer_app_config(state.prompt, state.app_config)
	if state.app_config.title:
		state.design_brief["product_name"] = state.app_config.title
	if state.app_config.subtitle:
		state.design_brief["one_liner"] = state.app_config.subtitle
	write_brief(pid, state.design_brief)

	preview = dict(state.plan_preview or {})
	preview["source_room"] = source_room_brief(preview)
	state.plan_preview = preview
	state.phase = "plan"
	state.status = "planning"
	save_state(state)

	def plan_target(_job):
		return run_plan_open(pid)

	try:
		start_job(pid, "agent_chat", label="Agent", target=plan_target)
	except JobConflictError:
		pass
	return load_state(pid)


def start_agent_chat(project_id: str, message: str) -> ProjectState:
	"""Append user message; Prime replies; Simulacra observes structured request."""
	state = load_state(project_id)
	state.chat.append(ChatMessage(role="user", content=message))
	if state.phase == "plan":
		state.prompt = _merge_prompt_update(state.prompt, message)
	state.design_brief = merge_notes_from_message(state.design_brief, message)
	write_brief(project_id, state.design_brief)
	save_state(state)

	def target(_job):
		return _agent_chat_turn(project_id, message=message, open_turn=False)

	try:
		start_job(project_id, "agent_chat", label="Agent", target=target)
	except JobConflictError as exc:
		raise ValueError(str(exc)) from exc
	return load_state(project_id)


def start_plan_chat(project_id: str, message: str) -> ProjectState:
	"""Alias — main chat is Prime regardless of phase."""
	return start_agent_chat(project_id, message)


def plan_chat(project_id: str, message: str) -> ProjectState:
	"""Synchronous chat turn (tests / scripts)."""
	state = load_state(project_id)
	state.chat.append(ChatMessage(role="user", content=message))
	if state.phase == "plan":
		state.prompt = _merge_prompt_update(state.prompt, message)
	save_state(state)
	return _agent_chat_turn(project_id, message=message, open_turn=False)


def _agent_chat_turn(
	project_id: str,
	*,
	message: str | None,
	open_turn: bool,
) -> ProjectState:
	state = load_state(project_id)
	root = project_dir(project_id)
	preview = state.plan_preview or {}
	files = list(preview.get("files") or [])
	rows = int(preview.get("row_count") or 0)
	high = int(preview.get("high_risk") or 0)
	vendors = list(preview.get("vendors") or [])

	if open_turn and any(m.role == "assistant" for m in state.chat):
		return state

	label = "Agent opening" if open_turn else "Agent"
	emit_event(
		project_id,
		"think",
		label=label,
		detail=(message or state.prompt)[:200],
		status="running",
	)

	turn = prime_chat_turn(
		root,
		state,
		message=message,
		open_turn=open_turn,
		project_id=project_id,
	)
	meta = turn.meta
	source = "prime" if turn.reply and meta.source == "prime" else ("error" if meta.error else "heuristic")

	cfg = turn.config
	if cfg and cfg.title:
		state.app_config = cfg
	elif open_turn:
		state.app_config = infer_app_config(state.prompt, state.app_config)

	if state.app_config.title:
		state.design_brief["product_name"] = state.app_config.title
	if state.app_config.subtitle:
		state.design_brief["one_liner"] = state.app_config.subtitle

	reply = turn.reply
	if not reply:
		if open_turn:
			reply = _open_reply(state, files=files, rows=rows, high=high, vendors=vendors)
		else:
			reply = _heuristic_chat_reply(state, message or "")
		# Prefer honest heuristic label over "error" when we still have a user-facing reply
		source = "heuristic"

	request = turn.request if turn.meta.source == "prime" and turn.reply else "await_user"

	state.chat.append(ChatMessage(role="assistant", content=reply, source=source))
	state.prime = {
		**state.prime,
		"session_id": meta.session_id or state.prime.get("session_id"),
		"model": meta.model or state.prime.get("model"),
		"source": source,
		"status": "chat",
		"last_error": meta.error,
		"request": request,
		"brief": turn.brief,
	}
	write_brief(project_id, state.design_brief)
	save_state(state)

	emit_event(
		project_id,
		"think",
		label="Agent replied",
		detail=f"{source} · request={request}",
		status="done",
	)
	emit_event(
		project_id,
		"done",
		label="Agent ready",
		detail=request,
		status="done",
	)

	# Observe: iterate while artifact exists — run inside this job (one builder).
	if request == "iterate" and state.phase == "ready":
		from .pipeline import _iterate_ui

		brief = (turn.brief or message or "").strip()
		if brief:
			emit_event(project_id, "phase", label="Agent requested iterate", detail=brief[:120], status="running")
			_iterate_ui(project_id, brief)
			state = load_state(project_id)
			state.prime = {**state.prime, "request": "await_user"}
			save_state(state)

	if request == "research":
		emit_event(
			project_id,
			"think",
			label="Agent requested research",
			detail=(turn.brief or message or "")[:200],
			status="done",
		)

	return load_state(project_id)


def approve_plan(project_id: str) -> ProjectState:
	state = load_state(project_id)
	if state.phase not in ("plan", "ready", "build"):
		raise ValueError("Project cannot be deepened from this phase")
	state.plan_approved = True
	if state.phase == "plan":
		state.phase = "build"
		state.status = "approved"
	# Clear build request — user confirmed via Build button
	state.prime = {**state.prime, "request": None}
	state.chat.append(
		ChatMessage(
			role="assistant",
			content="Building your app…",
			source="system",
		)
	)
	save_state(state)
	return state


def _merge_prompt_update(prompt: str, message: str) -> str:
	lower = message.lower()
	if any(w in lower for w in ("add", "include", "focus", "show", "build", "make", "game", "learn")):
		return f"{prompt}\n\n{message}".strip()
	return prompt


def _open_reply(
	state: ProjectState,
	*,
	files: list[dict[str, Any]],
	rows: int,
	high: int,
	vendors: list[str],
) -> str:
	"""Fallback opening when Prime is offline."""
	_ = high
	title = state.app_config.title or "Your project"
	names = ", ".join(f.get("name", "?") for f in files[:6])
	if not files:
		room = "No sources attached yet."
	else:
		room = f"Attached: `{names}`"
		if rows:
			room += f" ({rows} rows"
			if vendors:
				room += f" · {len(vendors)} vendors"
			room += ")."
	return (
		f"**{title}** — {state.app_config.subtitle}\n\n"
		f"{room}\n\n"
		"Chat with me to steer: upload files, use the sample pack, "
		"or ask me to research / gather material for your topic. "
		"When you’re ready, hit **Build**."
	)


def _heuristic_chat_reply(state: ProjectState, message: str) -> str:
	lower = message.lower()
	preview = state.plan_preview or {}
	rows = preview.get("row_count", 0)
	vendors = preview.get("vendors", [])
	title = state.app_config.title or "Your project"

	if "@" in message:
		tags = [t.strip() for t in message.split() if t.startswith("@")]
		if tags:
			return f"Tagged {', '.join(tags)}. I’ll use those sources in the build."

	# Prefer honest “agent hiccup” over misleading keyword matches when Prime failed
	if any(w in lower for w in ("research", "scrape", "web", "gather", "online")):
		return (
			f"**{title}** — the agent couldn’t finish that turn. "
			"Send again (research outline, sources to fetch), or upload files"
			+ (" / hit **Build** when ready." if state.phase == "plan" else ".")
		)

	if any(w in lower for w in ("how many", "count", "rows", "findings")) and rows:
		return f"The sources contain **{rows}** extracted rows ({preview.get('high_risk', 0)} high risk)."

	if lower.startswith(("what vendors", "which vendors", "list vendors", "who are the vendors")) and vendors:
		return f"Vendors in scope: **{', '.join(vendors[:8])}**."

	if any(w in lower for w in ("file", "source", "data room", "upload")):
		files = preview.get("files", [])
		names = ", ".join(f["name"] for f in files[:6]) or "none yet"
		return f"Source files: {names}."

	if state.phase == "ready":
		return (
			f"**{title}** — the agent hiccuped on that turn. "
			"Send the change again (e.g. denser summary, tighter opening) and I’ll retry."
		)

	return (
		f"Noted for **{title}**. "
		f"Keep chatting (sources, research, scope), or hit **Build** when ready."
	)
