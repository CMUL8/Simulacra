"""Run event log + live SSE subscribers."""

from __future__ import annotations

import json
import queue
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runs import project_dir

_subscribers: dict[str, list[queue.Queue[dict[str, Any]]]] = defaultdict(list)


def _events_path(project_id: str) -> Path:
	return project_dir(project_id) / "audit" / "events.jsonl"


def emit_event(
	project_id: str,
	event_type: str,
	*,
	label: str,
	detail: str = "",
	status: str = "running",
	meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
	evt: dict[str, Any] = {
		"id": f"evt_{uuid.uuid4().hex[:10]}",
		"ts": datetime.now(UTC).isoformat(),
		"type": event_type,
		"label": label,
		"detail": detail,
		"status": status,
	}
	if meta:
		evt["meta"] = meta

	path = _events_path(project_id)
	path.parent.mkdir(parents=True, exist_ok=True)
	with path.open("a", encoding="utf-8") as f:
		f.write(json.dumps(evt, default=str) + "\n")

	for q in _subscribers.get(project_id, []):
		try:
			q.put_nowait(evt)
		except queue.Full:
			pass
	return evt


def emit_prime_event(project_id: str, raw: dict[str, Any]) -> dict[str, Any] | None:
	"""Map builder RPC events → faint progress/action lines for the chat UI."""
	kind = raw.get("type", "")
	if kind == "tool_execution_start":
		tool = str(raw.get("tool") or raw.get("name") or "tool")
		label = _friendly_tool_start(tool, raw)
		return emit_event(
			project_id,
			"tool",
			label=label,
			detail=str(raw.get("input") or raw.get("args") or "")[:500],
			status="running",
			meta={"tool": tool, "action": "start"},
		)
	if kind == "tool_execution_end":
		tool = str(raw.get("tool") or raw.get("name") or "tool")
		ok = raw.get("success", True)
		return emit_event(
			project_id,
			"tool",
			label=_friendly_tool_done(tool, ok),
			detail=str(raw.get("output") or raw.get("result") or "")[:500],
			status="done" if ok else "fail",
			meta={"tool": tool, "action": "end"},
		)
	if kind == "agent_start":
		return emit_event(project_id, "phase", label="Session ready", status="running")
	if kind == "agent_end":
		return emit_event(project_id, "phase", label="Turn finished", status="done")
	if kind in ("assistant_message", "message"):
		text = raw.get("text") or raw.get("content") or ""
		if text and len(str(text)) > 20:
			snippet = str(text).strip().replace("\n", " ")
			if len(snippet) > 96:
				snippet = snippet[:93] + "…"
			return emit_event(
				project_id,
				"think",
				label=snippet,
				detail=str(text)[:800],
				status="done",
				meta={"action": "reason"},
			)
	return None


def _friendly_tool_start(tool: str, raw: dict[str, Any]) -> str:
	t = tool.lower()
	inp = str(raw.get("input") or raw.get("args") or "")
	path = ""
	for key in ("path", "file", "file_path", "target"):
		if key in inp:
			m = re.search(rf'["\']?{key}["\']?\s*[:=]\s*["\']([^"\']+)', inp, re.I)
			if m:
				path = m.group(1).split("/")[-1]
				break
	if "read" in t or t in ("cat", "open"):
		return f"Reading {path}" if path else "Reading files"
	if any(x in t for x in ("write", "edit", "str_replace", "apply_diff", "patch")):
		return f"Editing {path}" if path else "Editing files"
	if "search" in t or "grep" in t or "glob" in t:
		return "Searching codebase"
	if "bash" in t or "shell" in t or "terminal" in t:
		return "Running command"
	if "web" in t or "fetch" in t or "browse" in t:
		return "Fetching from the web"
	return f"Using {tool}"


def _friendly_tool_done(tool: str, ok: bool) -> str:
	t = tool.lower()
	if not ok:
		return f"Failed: {tool}"
	if "read" in t:
		return "Read files"
	if any(x in t for x in ("write", "edit", "str_replace", "apply_diff", "patch")):
		return "Updated files"
	if "search" in t or "grep" in t:
		return "Search done"
	if "bash" in t or "shell" in t:
		return "Command finished"
	if "web" in t or "fetch" in t:
		return "Fetch done"
	return tool


def list_events(project_id: str, *, tail: int = 200) -> list[dict[str, Any]]:
	path = _events_path(project_id)
	if not path.exists():
		return []
	lines = path.read_text(encoding="utf-8").strip().splitlines()
	out: list[dict[str, Any]] = []
	for line in lines[-tail:]:
		try:
			out.append(json.loads(line))
		except json.JSONDecodeError:
			continue
	return out


def subscribe(project_id: str) -> queue.Queue[dict[str, Any]]:
	q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=500)
	_subscribers[project_id].append(q)
	return q


def unsubscribe(project_id: str, q: queue.Queue[dict[str, Any]]) -> None:
	subs = _subscribers.get(project_id, [])
	if q in subs:
		subs.remove(q)
