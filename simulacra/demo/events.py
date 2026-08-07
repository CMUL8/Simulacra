"""Run event log + live SSE subscribers."""

from __future__ import annotations

import json
import queue
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
	"""Map Prime RPC events to Simulacra trace events."""
	kind = raw.get("type", "")
	if kind == "tool_execution_start":
		tool = raw.get("tool") or raw.get("name") or "tool"
		return emit_event(
			project_id,
			"tool",
			label=f"Running {tool}",
			detail=str(raw.get("input") or raw.get("args") or "")[:500],
			status="running",
		)
	if kind == "tool_execution_end":
		tool = raw.get("tool") or raw.get("name") or "tool"
		ok = raw.get("success", True)
		return emit_event(
			project_id,
			"tool",
			label=f"{tool}",
			detail=str(raw.get("output") or raw.get("result") or "")[:500],
			status="done" if ok else "fail",
		)
	if kind == "agent_start":
		return emit_event(project_id, "phase", label="Agent started", status="running")
	if kind == "agent_end":
		return emit_event(project_id, "phase", label="Agent finished", status="done")
	if kind in ("assistant_message", "message"):
		text = raw.get("text") or raw.get("content") or ""
		if text and len(str(text)) > 20:
			return emit_event(
				project_id,
				"think",
				label="Reasoning",
				detail=str(text)[:800],
				status="done",
			)
	return None


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
