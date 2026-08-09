"""Plan + bootstrap entry — scan sources, then fast preview (Prime deepen is separate)."""

from __future__ import annotations

import json
from typing import Any

from .chat import infer_app_config
from .design_brief import write_brief
from .extract import extract_data_room_report, write_summary
from .events import emit_event
from .jobs import JobConflictError, start_job
from .prime_hook import prime_open_plan, prime_plan_chat
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
	}
	state.row_count = len(rows)
	state.status = "planning"
	state.prime = {**state.prime, "status": "starting", "source": "pending"}
	state.design_brief = apply_profile_to_brief(state.design_brief or {}, profile)
	write_brief(pid, state.design_brief)
	write_agent_context(
		pid, rows=rows, profile=profile, extract=report, prompt=state.prompt
	)
	(root / "work" / "plan_preview.json").write_text(json.dumps(state.plan_preview, indent=2, default=str))
	detail = f"{len(rows)} rows · {len(files)} sources"
	if report.errors:
		detail += f" · {len(report.errors)} extract errors"
	emit_event(pid, "phase", label="Scanning sources", detail=detail, status="done")
	save_state(state)
	return state


def run_plan_open(project_id: str) -> ProjectState:
	"""Hand the opening turn to Prime. Called from a background job."""
	state = load_state(project_id)
	root = project_dir(project_id)
	preview = state.plan_preview or {}
	summary = str(preview.get("summary") or "")
	files = list(preview.get("files") or [])
	rows = int(preview.get("row_count") or 0)
	high = int(preview.get("high_risk") or 0)
	vendors = list(preview.get("vendors") or [])

	# Avoid duplicate opening replies on retry
	if any(m.role == "assistant" for m in state.chat):
		return state

	emit_event(project_id, "think", label="Prime planning", detail="Opening plan with Prime", status="running")
	cfg, reply, meta = prime_open_plan(root, state, summary=summary, project_id=project_id)
	source = "prime" if reply and meta.source == "prime" else ("error" if meta.error else "heuristic")

	if cfg and cfg.title:
		state.app_config = cfg
	else:
		state.app_config = infer_app_config(state.prompt, state.app_config)

	if state.app_config.title:
		state.design_brief["product_name"] = state.app_config.title
	if state.app_config.subtitle:
		state.design_brief["one_liner"] = state.app_config.subtitle

	if not reply:
		reply = _open_reply(state, files=files, rows=rows, high=high, vendors=vendors)
		source = "heuristic" if not meta.error else "error"

	state.chat.append(ChatMessage(role="assistant", content=reply, source=source))
	state.prime = {
		**state.prime,
		"session_id": meta.session_id or state.prime.get("session_id"),
		"model": meta.model or state.prime.get("model"),
		"source": source,
		"status": "plan_open",
		"last_error": meta.error,
	}
	emit_event(
		project_id,
		"think",
		label="Plan ready",
		detail=f"{source}: {state.app_config.title}",
		status="done",
	)
	emit_event(project_id, "done", label="Plan ready", detail="Approve when ready to build", status="done")
	write_brief(project_id, state.design_brief)
	save_state(state)
	return state


def init_plan(state: ProjectState) -> ProjectState:
	"""Scan sources, then open Prime-backed plan chat. User steers; Build starts the scaffold."""
	if not any(m.role == "user" for m in state.chat):
		state.chat.append(ChatMessage(role="user", content=state.prompt, source="system"))
		save_state(state)
	state = explore_plan_scan(state)
	pid = state.id

	# Infer title early so the shell isn't blank while plan-open runs
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

	# Always connect the user to Prime in chat first — free reign to ask, upload, or research.
	def plan_target(_job):
		return run_plan_open(pid)

	try:
		start_job(pid, "plan_ask", label="Planning with agent", target=plan_target)
	except JobConflictError:
		pass
	return load_state(pid)


def plan_chat(project_id: str, message: str) -> ProjectState:
	"""Synchronous plan turn (tests / scripts). Prefer start_plan_chat for the API."""
	state = load_state(project_id)
	if state.phase != "plan":
		raise ValueError("Project is not in plan phase")

	state.chat.append(ChatMessage(role="user", content=message))
	state.prompt = _merge_prompt_update(state.prompt, message)
	save_state(state)
	return _plan_chat_reply(project_id, message)


def start_plan_chat(project_id: str, message: str) -> ProjectState:
	"""Append the user turn immediately, then answer with Prime in a background job."""
	state = load_state(project_id)
	if state.phase != "plan":
		raise ValueError("Project is not in plan phase")

	state.chat.append(ChatMessage(role="user", content=message))
	state.prompt = _merge_prompt_update(state.prompt, message)
	save_state(state)

	def target(_job):
		return _plan_chat_reply(project_id, message)

	try:
		start_job(project_id, "plan_ask", label="Planning", target=target)
	except JobConflictError as exc:
		raise ValueError(str(exc)) from exc
	return load_state(project_id)


def _plan_chat_reply(project_id: str, message: str) -> ProjectState:
	state = load_state(project_id)
	emit_event(project_id, "think", label="Planning", detail=message[:200], status="running")
	reply = prime_plan_chat(project_dir(project_id), state, message, project_id=project_id)
	source = "prime"
	if not reply:
		reply = _heuristic_plan_reply(state, message)
		source = "heuristic"
	emit_event(project_id, "think", label="Plan response", status="done")

	state.chat.append(ChatMessage(role="assistant", content=reply, source=source))
	state.prime["source"] = source
	prev_title, prev_sub = state.app_config.title, state.app_config.subtitle
	state.app_config = infer_app_config(state.prompt, state.app_config)
	lower = message.lower()
	if "call it" not in lower and "rename to" not in lower and prev_title:
		state.app_config.title = prev_title
		state.app_config.subtitle = prev_sub
	from .design_brief import merge_notes_from_message

	state.design_brief = merge_notes_from_message(state.design_brief, message)
	write_brief(project_id, state.design_brief)
	save_state(state)
	return state


def approve_plan(project_id: str) -> ProjectState:
	state = load_state(project_id)
	if state.phase not in ("plan", "ready", "build"):
		raise ValueError("Project cannot be deepened from this phase")
	state.plan_approved = True
	if state.phase == "plan":
		state.phase = "build"
		state.status = "approved"
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
	"""Fallback opening when Prime is offline — still invite the user to steer."""
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


def _heuristic_plan_reply(state: ProjectState, message: str) -> str:
	lower = message.lower()
	preview = state.plan_preview
	rows = preview.get("row_count", 0)
	vendors = preview.get("vendors", [])

	if "@" in message:
		tags = [t.strip() for t in message.split() if t.startswith("@")]
		if tags:
			return f"Tagged {', '.join(tags)}. I’ll use those sources in the build."

	if any(w in lower for w in ("how many", "count", "rows", "findings")):
		return f"The sources contain **{rows}** extracted rows ({preview.get('high_risk', 0)} high risk)."

	if any(w in lower for w in ("vendor", "who")):
		return f"Vendors in scope: **{', '.join(vendors[:8]) or 'none'}**."

	if any(w in lower for w in ("file", "source", "data room")):
		files = preview.get("files", [])
		names = ", ".join(f["name"] for f in files[:6])
		return f"Source files: {names}."

	return (
		f"Noted for **{state.app_config.title}**. "
		f"Keep steering in chat (sources, research, scope), or hit **Build** when ready."
	)
