"""Observe → intervene layer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from simulacra.demo import paths as paths_mod
from simulacra.demo import runs as runs_mod
from simulacra.demo.observe import (
	assert_promotable,
	detect_topic_mismatch,
	duplicate_project_warnings,
	ensure_fresh_extract,
	ensure_research_scratch,
	promote_work_artifacts,
	snapshot_work_mtimes,
)
from simulacra.demo.runs import create_project, project_dir, save_state
from simulacra.demo.sources import SourceError, data_room_dir


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
	state = create_project("BJP political landscape briefing", use_fixture=True)
	return state.id


def test_promote_csv_from_work(project: str) -> None:
	pid = project
	root = project_dir(pid)
	work = root / "work"
	work.mkdir(parents=True, exist_ok=True)
	before = snapshot_work_mtimes(pid)
	(work / "extra_findings.csv").write_text("a,b\n1,2\n", encoding="utf-8")
	result = promote_work_artifacts(pid, before=before, force=False)
	assert "extra_findings.csv" in result["promoted"]
	assert (data_room_dir(pid) / "extra_findings.csv").is_file()
	assert result["quarantined"] == []


def test_internal_runtime_files_never_promoted(project: str) -> None:
	pid = project
	work = project_dir(pid) / "work"
	work.mkdir(parents=True, exist_ok=True)
	room = data_room_dir(pid)
	room.mkdir(parents=True, exist_ok=True)
	(work / "design_brief.json").write_text('{"a":1}\n', encoding="utf-8")
	(work / "kernel-state.json").write_text('{"b":2}\n', encoding="utf-8")
	(room / "design_brief.json").write_text('{"leaked":true}\n', encoding="utf-8")
	before = snapshot_work_mtimes(pid)
	result = promote_work_artifacts(pid, before=before, force=True)
	assert "design_brief.json" not in result["promoted"]
	assert "kernel-state.json" not in result["promoted"]
	assert not (room / "design_brief.json").exists()


def test_promote_research_dir_without_research_in_name(project: str) -> None:
	"""Files under work/research/ promote even if named timeline.json."""
	pid = project
	root = project_dir(pid)
	research = root / "work" / "research"
	research.mkdir(parents=True, exist_ok=True)
	before = snapshot_work_mtimes(pid)
	(research / "01_timeline.json").write_text('{"events":[]}\n', encoding="utf-8")
	result = promote_work_artifacts(pid, before=before, force=False)
	assert "01_timeline.json" in result["promoted"]
	assert (data_room_dir(pid) / "01_timeline.json").is_file()


def test_quarantine_env(project: str) -> None:
	pid = project
	work = project_dir(pid) / "work"
	work.mkdir(parents=True, exist_ok=True)
	env_path = work / ".env"
	env_path.write_text("SECRET=1\n", encoding="utf-8")
	with pytest.raises(SourceError):
		assert_promotable(env_path)
	before = snapshot_work_mtimes(pid)
	result = promote_work_artifacts(pid, before=before, force=True)
	assert ".env" not in result["promoted"]
	assert any("env" in q.lower() for q in result["quarantined"])
	assert (work / "quarantine").is_dir()
	assert not (data_room_dir(pid) / ".env").exists()


def test_topic_mismatch_vendor_vs_bjp(project: str) -> None:
	from simulacra.demo.runs import load_state

	pid = project
	state = load_state(pid)
	# Fixture pack looks like vendor sample; prompt is BJP
	state.prompt = "Build a BJP political landscape briefing for strategy"
	preview = dict(state.plan_preview or {})
	preview["files"] = [
		{"name": "notes.json"},
		{"name": "supplement.csv"},
		{"name": "vendor-research.md"},
	]
	preview["row_count"] = 10
	state.plan_preview = preview
	save_state(state)
	state = load_state(pid)
	mismatch = detect_topic_mismatch(state)
	assert mismatch is not None
	assert mismatch["ok"] is False
	assert mismatch["looks_like_vendor_sample"] is True
	assert "BJP" in mismatch["prompt_topic"] or "bjp" in mismatch["prompt_topic"].lower()


def test_fingerprint_stale_triggers_reingest(project: str, monkeypatch: pytest.MonkeyPatch) -> None:
	from simulacra.demo.runs import load_state

	pid = project
	state = load_state(pid)
	preview = dict(state.plan_preview or {})
	preview["fingerprint"] = "stale-fingerprint"
	state.plan_preview = preview
	state.phase = "plan"
	save_state(state)

	called = MagicMock(side_effect=lambda s: s)
	monkeypatch.setattr("simulacra.demo.plan.explore_plan_scan", called)
	assert ensure_fresh_extract(pid) is True
	called.assert_called_once()


def test_duplicate_warn(project: str) -> None:
	from simulacra.demo.runs import load_state

	pid = project
	state = load_state(pid)
	prompt = state.prompt
	tid = state.tenant_id
	# Same prompt, different id → warn
	warnings = duplicate_project_warnings(tid, prompt, exclude_id="not-this-id")
	assert warnings
	assert "Similar project" in warnings[0]
	# Excluding the only match → no warn
	assert duplicate_project_warnings(tid, prompt, exclude_id=pid) == []


def test_research_scratch_exists(project: str) -> None:
	pid = project
	path = ensure_research_scratch(pid)
	assert path.is_dir()
	assert (path / "README.md").is_file()
	assert "work/research" in str(path).replace("\\", "/")
