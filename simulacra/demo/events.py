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

# Labels that must never reach the faint activity feed.
_BAD_LABELS = frozenset({"tool", "using tool", "using", "unknown", "unknown tool"})


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
	"""Map builder RPC events → faint progress/action lines for the chat UI.

	Prime Agent RPC shapes (docs/rpc.md):
	  tool_execution_start: { type, toolCallId, toolName, args: {...} }
	  tool_execution_end:   { type, toolCallId, toolName, result, isError }
	  message_update:       streaming text/thinking deltas (ignored here)
	"""
	kind = raw.get("type", "")
	if kind == "tool_execution_start":
		tool = _extract_tool_name(raw)
		if not tool:
			return None
		label = _friendly_tool_start(tool, raw)
		if not label or label.lower() in _BAD_LABELS:
			return None
		args = _tool_args(raw)
		return emit_event(
			project_id,
			"tool",
			label=label,
			detail=_detail_from_args(args)[:500],
			status="running",
			meta={"tool": tool, "action": "start", "toolCallId": raw.get("toolCallId")},
		)
	if kind == "tool_execution_end":
		# Prefer one live start line — only surface failures (avoid start+end stacks).
		tool = _extract_tool_name(raw)
		is_err = bool(raw.get("isError") or raw.get("success") is False)
		if not is_err:
			return None
		label = _friendly_tool_done(tool or "action", ok=False)
		if not label or label.lower() in _BAD_LABELS:
			return None
		return emit_event(
			project_id,
			"tool",
			label=label,
			detail=str(raw.get("result") or raw.get("output") or "")[:500],
			status="fail",
			meta={"tool": tool, "action": "end", "toolCallId": raw.get("toolCallId")},
		)
	if kind == "agent_start":
		# Silent — UI already shows a wait stage; avoid "Session ready" spam.
		return None
	if kind == "agent_end":
		return None
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


def _extract_tool_name(raw: dict[str, Any]) -> str:
	"""Pull tool name from flat or nested RPC payloads."""
	for key in ("toolName", "tool_name", "tool", "name"):
		v = raw.get(key)
		if isinstance(v, str) and v.strip() and v.strip().lower() not in ("tool", "unknown"):
			return v.strip()
	for nest_key in ("toolCall", "tool_call", "call", "data", "payload"):
		nested = raw.get(nest_key)
		if isinstance(nested, dict):
			found = _extract_tool_name(nested)
			if found:
				return found
	return ""


def _tool_args(raw: dict[str, Any]) -> dict[str, Any]:
	for key in ("args", "input", "arguments", "params"):
		v = raw.get(key)
		if isinstance(v, dict):
			return v
		if isinstance(v, str) and v.strip().startswith("{"):
			try:
				parsed = json.loads(v)
				if isinstance(parsed, dict):
					return parsed
			except json.JSONDecodeError:
				pass
	for nest_key in ("toolCall", "tool_call", "call", "data"):
		nested = raw.get(nest_key)
		if isinstance(nested, dict):
			found = _tool_args(nested)
			if found:
				return found
	return {}


def _detail_from_args(args: dict[str, Any]) -> str:
	try:
		return json.dumps(args, default=str)
	except TypeError:
		return str(args)


def _basename(path: str) -> str:
	cleaned = path.strip().rstrip("/")
	if not cleaned:
		return ""
	return cleaned.split("/")[-1]


def _path_hint(args: dict[str, Any]) -> str:
	for key in (
		"path",
		"file",
		"file_path",
		"filePath",
		"target",
		"target_file",
		"filename",
		"notebook_path",
	):
		v = args.get(key)
		if isinstance(v, str) and v.strip():
			return _basename(v)
	# Sometimes paths arrive as a list.
	for key in ("paths", "files"):
		v = args.get(key)
		if isinstance(v, list) and v and isinstance(v[0], str):
			return _basename(v[0])
	return ""


def _path_from_raw_string(inp: str) -> str:
	"""Legacy string-args fallback (regex over serialized input)."""
	for key in ("path", "file", "file_path", "filePath", "target", "target_file"):
		m = re.search(rf'["\']?{key}["\']?\s*[:=]\s*["\']([^"\']+)', inp, re.I)
		if m:
			return _basename(m.group(1))
	return ""


def _friendly_tool_start(tool: str, raw: dict[str, Any]) -> str:
	t = tool.lower().replace("-", "_")
	args = _tool_args(raw)
	path = _path_hint(args)
	if not path:
		path = _path_from_raw_string(_detail_from_args(args) if args else str(raw.get("input") or ""))

	if t in ("read", "read_file", "cat", "open") or "read" in t:
		return f"Reading {path}" if path else "Reading files"
	if any(x in t for x in ("write", "edit", "str_replace", "apply_diff", "patch", "create_file")):
		return f"Editing {path}" if path else "Editing files"
	if "web" in t or t in ("web_search", "websearch", "search_web"):
		return "Searching web"
	if "fetch" in t or "browse" in t or "http" in t:
		return "Fetching from the web"
	if "search" in t or "grep" in t or "glob" in t or t in ("rg",):
		return "Searching codebase"
	if any(x in t for x in ("bash", "shell", "terminal", "run_terminal", "exec")):
		return "Running command"
	# Real tool name only — never "Using tool".
	pretty = tool.replace("_", " ").strip()
	if not pretty or pretty.lower() in _BAD_LABELS:
		return ""
	return pretty[0].upper() + pretty[1:] if pretty else ""


def _friendly_tool_done(tool: str, ok: bool) -> str:
	t = (tool or "").lower().replace("-", "_")
	if not ok:
		pretty = tool.replace("_", " ").strip() if tool else "action"
		if pretty.lower() in _BAD_LABELS:
			pretty = "action"
		return f"Failed: {pretty}"
	if "read" in t:
		return "Read files"
	if any(x in t for x in ("write", "edit", "str_replace", "apply_diff", "patch")):
		return "Updated files"
	if "web" in t:
		return "Search done"
	if "search" in t or "grep" in t or "glob" in t:
		return "Search done"
	if "bash" in t or "shell" in t:
		return "Command finished"
	if "fetch" in t:
		return "Fetch done"
	pretty = tool.replace("_", " ").strip()
	if not pretty or pretty.lower() in _BAD_LABELS:
		return ""
	return pretty


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


def last_event(project_id: str) -> dict[str, Any] | None:
	evts = list_events(project_id, tail=1)
	return evts[-1] if evts else None


def subscribe(project_id: str) -> queue.Queue[dict[str, Any]]:
	q: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=500)
	_subscribers[project_id].append(q)
	return q


def unsubscribe(project_id: str, q: queue.Queue[dict[str, Any]]) -> None:
	subs = _subscribers.get(project_id, [])
	if q in subs:
		subs.remove(q)
