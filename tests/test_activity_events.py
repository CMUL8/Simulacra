"""Activity feed mapping for Prime RPC tool events."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulacra.demo import events as ev


@pytest.fixture()
def project_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
	pid = "proj_test_activity"
	root = tmp_path / pid
	(root / "audit").mkdir(parents=True)

	def _project_dir(project_id: str) -> Path:
		assert project_id == pid
		return root

	monkeypatch.setattr(ev, "project_dir", _project_dir)
	return pid


def test_tool_start_uses_toolName_and_path(project_id: str) -> None:
	raw = {
		"type": "tool_execution_start",
		"toolCallId": "call_1",
		"toolName": "read",
		"args": {"path": "app/src/notes.json"},
	}
	out = ev.emit_prime_event(project_id, raw)
	assert out is not None
	assert out["label"] == "Reading notes.json"
	assert "Using tool" not in out["label"]
	assert out["label"].lower() != "tool"
	assert out["meta"]["tool"] == "read"


def test_missing_tool_name_emits_nothing(project_id: str) -> None:
	raw = {"type": "tool_execution_start", "toolCallId": "call_x", "args": {"path": "a.ts"}}
	assert ev.emit_prime_event(project_id, raw) is None


def test_legacy_tool_field_still_works(project_id: str) -> None:
	raw = {"type": "tool_execution_start", "tool": "bash", "args": {"command": "ls"}}
	out = ev.emit_prime_event(project_id, raw)
	assert out is not None
	assert out["label"] == "Running command"


def test_nested_tool_call_name(project_id: str) -> None:
	raw = {
		"type": "tool_execution_start",
		"toolCall": {"toolName": "edit", "args": {"path": "App.tsx"}},
	}
	out = ev.emit_prime_event(project_id, raw)
	assert out is not None
	assert out["label"] == "Editing App.tsx"


def test_web_and_search_labels(project_id: str) -> None:
	web = ev.emit_prime_event(
		project_id,
		{"type": "tool_execution_start", "toolName": "web_search", "args": {"query": "x"}},
	)
	assert web is not None
	assert web["label"] == "Searching web"

	grep = ev.emit_prime_event(
		project_id,
		{"type": "tool_execution_start", "toolName": "grep", "args": {"pattern": "foo"}},
	)
	assert grep is not None
	assert grep["label"] == "Searching codebase"


def test_successful_end_suppressed(project_id: str) -> None:
	raw = {
		"type": "tool_execution_end",
		"toolCallId": "call_1",
		"toolName": "read",
		"result": {"content": []},
		"isError": False,
	}
	assert ev.emit_prime_event(project_id, raw) is None


def test_failed_end_surfaces(project_id: str) -> None:
	raw = {
		"type": "tool_execution_end",
		"toolCallId": "call_1",
		"toolName": "bash",
		"isError": True,
		"result": {"content": [{"type": "text", "text": "boom"}]},
	}
	out = ev.emit_prime_event(project_id, raw)
	assert out is not None
	assert out["status"] == "fail"
	assert "Failed" in out["label"]
	assert out["label"].lower() != "tool"


def test_agent_lifecycle_silent(project_id: str) -> None:
	assert ev.emit_prime_event(project_id, {"type": "agent_start"}) is None
	assert ev.emit_prime_event(project_id, {"type": "agent_end"}) is None


def test_friendly_never_using_tool() -> None:
	# Empty / garbage tool names must not produce the screenshot spam.
	assert ev._friendly_tool_start("tool", {}) == ""
	assert ev._friendly_tool_start("", {}) == ""
	label = ev._friendly_tool_start("read", {"args": {"path": "x.py"}})
	assert label == "Reading x.py"
	assert "Using tool" not in label


def test_last_event_helper(project_id: str) -> None:
	ev.emit_event(project_id, "think", label="Thinking", status="running")
	last = ev.last_event(project_id)
	assert last is not None
	assert last["label"] == "Thinking"


def test_audit_jsonl_written(project_id: str, tmp_path: Path) -> None:
	ev.emit_prime_event(
		project_id,
		{
			"type": "tool_execution_start",
			"toolName": "write",
			"args": {"path": "src/App.tsx"},
		},
	)
	path = tmp_path / project_id / "audit" / "events.jsonl"
	lines = path.read_text().strip().splitlines()
	assert len(lines) == 1
	row = json.loads(lines[0])
	assert row["label"] == "Editing App.tsx"
