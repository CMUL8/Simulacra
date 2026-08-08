"""Plan mode — read-only exploration before build. Prime owns the first reply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .chat import infer_app_config
from .extract import extract_data_room, write_summary
from .events import emit_event
from .prime_hook import prime_open_plan, prime_plan_chat
from .runs import ChatMessage, ProjectState, load_state, project_dir, save_state


def explore_plan_data(state: ProjectState) -> ProjectState:
	"""Scan sources, then hand the user request to Prime for the opening plan."""
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
	sample = rows[:5]

	state.plan_preview = {
		"row_count": len(rows),
		"high_risk": high,
		"vendors": vendors,
		"files": files,
		"summary": summary,
		"sample_rows": sample,
	}
	state.row_count = len(rows)
	state.status = "planning"
	(root / "work" / "plan_preview.json").write_text(json.dumps(state.plan_preview, indent=2))
	emit_event(pid, "phase", label="Scanning sources", detail=f"{len(rows)} rows", status="done")

	emit_event(pid, "think", label="Prime planning", detail="Opening plan with Prime", status="running")
	cfg, reply, meta = prime_open_plan(root, state, summary=summary, project_id=pid)
	source = "prime" if reply and meta.source == "prime" else ("heuristic" if not reply else meta.source)

	if cfg and cfg.title:
		state.app_config = cfg
	else:
		state.app_config = infer_app_config(state.prompt, state.app_config)

	# Keep design brief product fields aligned with the proposed app
	if state.app_config.title:
		state.design_brief["product_name"] = state.app_config.title
	if state.app_config.subtitle:
		state.design_brief["one_liner"] = state.app_config.subtitle

	if not reply:
		reply = _heuristic_open_reply(state, files=files, rows=len(rows), high=high, vendors=vendors)
		source = "heuristic"

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
		pid,
		"think",
		label="Plan ready",
		detail=f"{source}: {state.app_config.title}",
		status="done",
	)
	emit_event(pid, "done", label="Plan ready", detail="Approve when ready to build", status="done")
	from .design_brief import write_brief

	write_brief(pid, state.design_brief)
	save_state(state)
	return state


def plan_chat(project_id: str, message: str) -> ProjectState:
	state = load_state(project_id)
	if state.phase != "plan":
		raise ValueError("Project is not in plan phase")

	state.chat.append(ChatMessage(role="user", content=message))
	state.prompt = _merge_prompt_update(state.prompt, message)

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
	# Keep Prime's proposed product name unless the user explicitly renames
	lower = message.lower()
	if "call it" not in lower and "rename to" not in lower and prev_title:
		state.app_config.title = prev_title
		state.app_config.subtitle = prev_sub
	from .design_brief import merge_notes_from_message, write_brief

	state.design_brief = merge_notes_from_message(state.design_brief, message)
	write_brief(project_id, state.design_brief)
	save_state(state)
	return state


def approve_plan(project_id: str) -> ProjectState:
	state = load_state(project_id)
	if state.phase != "plan":
		raise ValueError("Project is not in plan phase")
	state.plan_approved = True
	state.phase = "build"
	state.status = "approved"
	state.chat.append(
		ChatMessage(
			role="assistant",
			content="Plan approved. Building through Simulacra’s control layer…",
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


def _heuristic_open_reply(
	state: ProjectState,
	*,
	files: list[dict[str, Any]],
	rows: int,
	high: int,
	vendors: list[str],
) -> str:
	return (
		f"Here’s a plan for **{state.app_config.title}** — {state.app_config.subtitle}\n\n"
		f"I scanned **{len(files)}** source files"
		+ (f" ({rows} structured rows, {high} high-risk, {len(vendors)} vendors)" if rows else "")
		+ ".\n\n"
		"Refine the idea in chat, set Look & feel and **Save**, then click **Approve & Build** "
		"when you’re ready.\n\n"
		"_Prime was unavailable for this opening reply — using a local fallback._"
	)


def _heuristic_plan_reply(state: ProjectState, message: str) -> str:
	lower = message.lower()
	preview = state.plan_preview
	rows = preview.get("row_count", 0)
	vendors = preview.get("vendors", [])

	if "@" in message:
		tags = [t.strip() for t in message.split() if t.startswith("@")]
		if tags:
			return (
				f"Tagged {', '.join(tags)}. In plan mode I only **read** these sources — "
				f"the built app will draw from them through Simulacra’s control layer."
			)

	if any(w in lower for w in ("how many", "count", "rows", "findings")):
		return f"The sources contain **{rows}** extracted rows ({preview.get('high_risk', 0)} high risk)."

	if any(w in lower for w in ("vendor", "who")):
		return f"Vendors in scope: **{', '.join(vendors[:8]) or 'none'}**."

	if any(w in lower for w in ("file", "source", "data room")):
		files = preview.get("files", [])
		names = ", ".join(f["name"] for f in files[:6])
		return f"Source files: {names}."

	if any(w in lower for w in ("security", "access", "direct")):
		return (
			"**Control layer:** generated apps never access business systems directly. "
			"Simulacra mediates reads with auth, audit, and eval gates."
		)

	return (
		f"Noted for **{state.app_config.title}**. "
		f"I’ll fold that into the build. Approve when ready, or keep refining."
	)
