"""Stale multi-chat ids must not block continuing an old project."""

from __future__ import annotations

from pathlib import Path

import pytest

from simulacra.demo import paths as paths_mod
from simulacra.demo import runs as runs_mod
from simulacra.demo.runs import activate_chat, create_project, load_state, project_dir, save_state


@pytest.fixture()
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
	runs = tmp_path / "runs"
	runs.mkdir()
	monkeypatch.setattr(paths_mod, "RUNS_DIR", runs)
	monkeypatch.setattr(runs_mod, "RUNS_DIR", runs)
	monkeypatch.setattr(
		"simulacra.demo.tenants.assert_under_project_quota",
		lambda *_a, **_k: None,
	)
	monkeypatch.setattr(
		"simulacra.demo.tenants.assert_tenant_active",
		lambda *_a, **_k: None,
	)
	state = create_project("Continue congress report", use_fixture=False)
	return state.id


def test_activate_unknown_chat_heals(project: str) -> None:
	state = load_state(project)
	assert state.active_chat_id
	good = state.active_chat_id
	healed = activate_chat(project, "chat_deadbeef00")
	assert healed.active_chat_id == good
	assert healed.chats
	assert any(t.id == good for t in healed.chats)


def test_activate_valid_chat_still_switches(project: str) -> None:
	from simulacra.demo.runs import create_chat

	create_chat(project, title="Second")
	state = load_state(project)
	other = next(t.id for t in state.chats if t.id != state.active_chat_id)
	first = next(t.id for t in state.chats if t.id != other)
	switched = activate_chat(project, other)
	assert switched.active_chat_id == other
	back = activate_chat(project, first)
	assert back.active_chat_id == first


def test_delete_chat(project: str) -> None:
	from simulacra.demo.runs import create_chat, delete_chat

	create_chat(project, title="Extra")
	state = load_state(project)
	assert len(state.chats) >= 2
	victim = state.active_chat_id
	other = next(t.id for t in state.chats if t.id != victim)
	out = delete_chat(project, victim)
	assert victim not in {t.id for t in out.chats}
	assert out.active_chat_id == other
	# Cannot delete last remaining chat
	import pytest

	with pytest.raises(ValueError, match="only chat"):
		delete_chat(project, other)