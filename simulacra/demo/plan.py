"""Plan + bootstrap entry — scan sources, then fast preview (Prime deepen is separate)."""

from __future__ import annotations

import json
from typing import Any

from .chat import infer_app_config
from .design_brief import write_brief
from .extract import extract_data_room, write_summary
from .events import emit_event
from .jobs import JobConflictError, start_job
from .prime_hook import prime_open_plan, prime_plan_chat
from .runs import ChatMessage, ProjectState, load_state, project_dir, save_state


def explore_plan_scan(state: ProjectState) -> ProjectState:
	"""Read-only extract for plan preview — does not talk to the user yet."""
	root = project_dir(state.id)
	pid = state.id
	data_room = root / "inputs" / "data-room"
	emit_event(pid, "phase", label="Scanning sources", status="running")
	rows = extract_data_room(data_room)
	summary = write_summary(rows, state.prompt)

	files: list[dict[str, Any]] = []
	if data_room.exists():
		for p in sorted(data_room.rglob("*")):
			if p.is_file():
				files.append(
					{
						"name": str(p.relative_to(data_room)),
						"size": p.stat().st_size,
						"type": p.suffix.lstrip("."),
					}
				)

	vendors = sorted({r["vendor"] for r in rows})
	high = sum(1 for r in rows if r["risk_level"] == "high")

	state.plan_preview = {
		"row_count": len(rows),
		"high_risk": high,
		"vendors": vendors,
		"files": files,
		"summary": summary,
		"sample_rows": rows[:5],
	}
	state.row_count = len(rows)
	state.status = "planning"
	state.prime = {**state.prime, "status": "starting", "source": "pending"}
	(root / "work" / "plan_preview.json").write_text(json.dumps(state.plan_preview, indent=2))
	emit_event(pid, "phase", label="Scanning sources", detail=f"{len(rows)} rows", status="done")
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
	"""Scan immediately, then bootstrap a live preview (no Prime wait). See APP_MAKER_CONTRACT."""
	if not any(m.role == "user" for m in state.chat):
		state.chat.append(ChatMessage(role="user", content=state.prompt, source="system"))
		save_state(state)
	state = explore_plan_scan(state)
	pid = state.id

	# Infer title early so the shell isn't blank while bootstrap runs
	state.app_config = infer_app_config(state.prompt, state.app_config)
	if state.app_config.title:
		state.design_brief["product_name"] = state.app_config.title
	if state.app_config.subtitle:
		state.design_brief["one_liner"] = state.app_config.subtitle
	write_brief(pid, state.design_brief)
	save_state(state)

	def target(_job):
		from .pipeline import bootstrap_project

		return bootstrap_project(load_state(pid))

	try:
		start_job(pid, "bootstrap", label="Building preview", target=target)
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
	"""Clean opening when the planner cannot answer."""
	return (
		f"**{state.app_config.title}** — {state.app_config.subtitle}\n\n"
		f"I have {len(files)} source files ready"
		+ (f" ({rows} rows across {len(vendors)} vendors)" if rows else "")
		+ ".\n\n"
		"Review the plan, pick a style, open the **draft preview**, then **Build app**."
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
		f"I’ll fold that into the build. Open the draft preview, or hit **Build app**."
	)
