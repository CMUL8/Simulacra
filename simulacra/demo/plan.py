"""Plan mode — read-only data room exploration before build."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .chat import infer_app_config
from .extract import extract_data_room, write_summary
from .events import emit_event
from .prime_hook import prime_plan_chat
from .runs import ChatMessage, ProjectState, load_state, project_dir, save_state


def explore_plan_data(state: ProjectState) -> ProjectState:
	"""Read-only extract for plan preview — no app build."""
	root = project_dir(state.id)
	pid = state.id
	data_room = root / "inputs" / "data-room"
	emit_event(pid, "phase", label="Scanning data room", status="running")
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
	state.app_config = infer_app_config(state.prompt)
	state.status = "planning"

	(root / "work" / "plan_preview.json").write_text(json.dumps(state.plan_preview, indent=2))

	reply = (
		f"**Plan mode** — read-only exploration.\n\n"
		f"I scanned {len(files)} files and found **{len(rows)} findings** "
		f"({high} high risk) across {len(vendors)} vendors.\n\n"
		f"Proposed app: **{state.app_config.title}** — {state.app_config.subtitle}\n\n"
		f"Ask questions about the data, refine the prompt, or tag files with `@`. "
		f"When ready, click **Approve & Build**."
	)
	state.chat.append(ChatMessage(role="assistant", content=reply, source="system"))
	emit_event(pid, "phase", label="Scanning data room", detail=f"{len(rows)} findings", status="done")
	emit_event(pid, "done", label="Plan ready", detail="Approve when ready to build", status="done")
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
	state.app_config = infer_app_config(state.prompt, state.app_config)
	# Merge aesthetic hints from message into design brief
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
			content="Plan approved. Building your app through the integration control layer…",
		)
	)
	save_state(state)
	return state


def _merge_prompt_update(prompt: str, message: str) -> str:
	lower = message.lower()
	if any(w in lower for w in ("add", "include", "focus", "show", "build", "make")):
		return f"{prompt}\n\n{message}".strip()
	return prompt


def _heuristic_plan_reply(state: ProjectState, message: str) -> str:
	lower = message.lower()
	preview = state.plan_preview
	rows = preview.get("row_count", 0)
	vendors = preview.get("vendors", [])

	if "@" in message:
		tags = [t.strip() for t in message.split() if t.startswith("@")]
		if tags:
			return (
				f"Tagged {', '.join(tags)}. In plan mode I can only **read** these sources — "
				f"apps never access business systems directly. Data flows through Simulacra's "
				f"integration control layer with audit logging."
			)

	if any(w in lower for w in ("how many", "count", "rows", "findings")):
		return f"The data room contains **{rows} findings** ({preview.get('high_risk', 0)} high risk)."

	if any(w in lower for w in ("vendor", "who")):
		return f"Vendors in scope: **{', '.join(vendors[:8]) or 'none'}**."

	if any(w in lower for w in ("file", "source", "data room")):
		files = preview.get("files", [])
		names = ", ".join(f["name"] for f in files[:6])
		return f"Data room files: {names}. All reads are logged and sandboxed."

	if any(w in lower for w in ("security", "access", "direct")):
		return (
			"**Integration control layer:** generated apps never access business systems directly. "
			"Simulacra mediates every query through governed APIs with RBAC, audit trails, and eval gates."
		)

	return (
		f"Noted. I'll incorporate that into the build plan for **{state.app_config.title}**. "
		f"Current scope: {rows} findings. Refine further or approve when ready."
	)
