"""Unit tests for design brief + job bounds (no Prime required)."""

from __future__ import annotations

import time

from simulacra.demo.design_brief import default_brief, merge_brief, merge_notes_from_message
from simulacra.demo.jobs import BOUNDS, JobCancelled, JobRecord, check_bounds, _jobs, _lock


def test_default_brief_has_required_keys():
	b = default_brief(prompt="Build a vendor risk board")
	assert b["aesthetic"]["direction"]
	assert b["aesthetic"]["chrome"] == "no-cards"
	assert "must_have" in b["information_architecture"]


def test_merge_brief_patches_nested():
	base = default_brief()
	out = merge_brief(base, {"aesthetic": {"density": "dense", "palette": {"accent": "#112233"}}})
	assert out["aesthetic"]["density"] == "dense"
	assert out["aesthetic"]["palette"]["accent"] == "#112233"
	assert out["aesthetic"]["direction"] == base["aesthetic"]["direction"]


def test_merge_notes_from_message():
	b = default_brief()
	out = merge_notes_from_message(b, "Make it denser and dark mode with #3D8B6E accent")
	assert out["aesthetic"]["density"] == "dense"
	assert out["aesthetic"]["color_mode"] == "dark"
	assert out["aesthetic"]["palette"]["accent"] == "#3D8B6E"


def test_check_bounds_timeout():
	job = JobRecord(
		id="job_test",
		project_id="proj_test",
		kind="build_run",
		deadline=time.monotonic() - 1,
		max_steps=40,
	)
	with _lock:
		_jobs["proj_test"] = job
	try:
		try:
			check_bounds("proj_test")
			assert False, "expected JobCancelled"
		except JobCancelled as exc:
			assert "timeout" in str(exc)
	finally:
		with _lock:
			_jobs.pop("proj_test", None)


def test_bounds_table_complete():
	for kind in ("bootstrap", "plan_ask", "build_run", "iterate_run", "iterate_ask"):
		assert kind in BOUNDS
		assert BOUNDS[kind]["timeout"] > 0
		assert BOUNDS[kind]["max_steps"] > 0


def test_bootstrap_runs_prime(tmp_path, monkeypatch):
	"""Bootstrap scaffolds behind the scenes then runs the builder → phase=ready."""
	from simulacra.demo import pipeline as pipe
	from simulacra.demo import runs as runs_mod
	from simulacra.demo.runs import AppConfig, ProjectState, save_state

	pid = "proj_boot"
	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	for d in ("inputs/data-room", "outputs", "work", "app", "audit"):
		(tmp_path / pid / d).mkdir(parents=True)

	from types import SimpleNamespace

	rows = [
		{
			"vendor": "Acme",
			"theme": "SSO",
			"risk_level": "high",
			"risk_score": 85,
			"evidence": "SSO gap",
			"source_file": "note.md",
			"region": "",
			"owner": "",
		}
	]

	monkeypatch.setattr(
		pipe,
		"extract_data_room_report",
		lambda *_a, **_k: SimpleNamespace(rows=rows, errors=[], skipped=[]),
	)
	monkeypatch.setattr(pipe, "write_summary", lambda *_a, **_k: "summary")
	monkeypatch.setattr(pipe, "rows_to_parquet", lambda *_a, **_k: None)
	monkeypatch.setattr(pipe, "run_gates", lambda *_a, **_k: {"status": "pass", "results": []})
	monkeypatch.setattr(pipe, "write_manifest", lambda *_a, **_k: None)
	monkeypatch.setattr(pipe, "prepare_project_sandbox", lambda *_a, **_k: {"active": "none", "trust_model": "t"})
	monkeypatch.setattr(pipe, "get_tenant", lambda *_a, **_k: (_ for _ in ()).throw(KeyError()))
	monkeypatch.setattr(pipe, "write_agent_context", lambda *_a, **_k: {})
	monkeypatch.setattr(pipe, "apply_profile_to_brief", lambda brief, *_a, **_k: brief)
	monkeypatch.setattr(
		pipe,
		"profile_rows",
		lambda r: SimpleNamespace(high_risk=1, to_dict=lambda: {}),
	)
	called_prime = {"n": 0}

	def _prime(*_a, **_k):
		called_prime["n"] += 1
		return {
			"used": True,
			"ok": True,
			"source": "prime",
			"layout_customized": True,
			"style_only": False,
		}

	monkeypatch.setattr(pipe, "prime_build_app", _prime)
	monkeypatch.setattr(pipe, "sync_app", lambda *_a, **_k: tmp_path / pid / "app")
	monkeypatch.setattr(pipe, "apply_brief_css_tokens", lambda *_a, **_k: None)
	monkeypatch.setattr(pipe, "write_brief", lambda *_a, **_k: None)
	monkeypatch.setattr(pipe, "start_preview", lambda state, rows, app_dir=None: f"/projects/{pid}/preview/")
	monkeypatch.setattr(pipe, "save_checkpoint", lambda *_a, **_k: None)

	state = ProjectState(
		id=pid,
		prompt="vendor risk dashboard",
		phase="plan",
		status="planning",
		artifact_kind="data_app",
		app_config=AppConfig(title="Vendor Risk", subtitle="demo"),
		plan_preview={"files": [{"name": "a.md"}], "vendors": ["Acme"], "high_risk": 1, "row_count": 1},
	)
	save_state(state)

	out = pipe.bootstrap_project(runs_mod.load_state(pid))
	assert called_prime["n"] == 1
	assert out.phase == "ready"
	assert out.status == "ready"
	assert out.deploy_url == f"/projects/{pid}/preview/"
	assert out.prime.get("source") == "prime"
	assert out.chat[-1].source == "prime"
	assert "Preview is ready" in out.chat[-1].content
	assert "Prime" not in out.chat[-1].content
	assert "Build app" not in out.chat[-1].content


def test_deepen_calls_prime_when_preview_exists(tmp_path, monkeypatch):
	from simulacra.demo import pipeline as pipe
	from simulacra.demo import runs as runs_mod
	from simulacra.demo.runs import AppConfig, ProjectState, save_state

	pid = "proj_deep"
	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	for d in ("inputs/data-room", "outputs", "work", "app", "audit"):
		(tmp_path / pid / d).mkdir(parents=True)
	(tmp_path / pid / "outputs" / "table.parquet").write_bytes(b"")

	monkeypatch.setattr(pipe, "_load_rows", lambda *_a, **_k: [{"vendor": "A", "risk_level": "low"}])
	monkeypatch.setattr(
		pipe,
		"prime_build_app",
		lambda *_a, **_k: {"used": True, "ok": True, "source": "prime", "session_id": "s1", "model": "m"},
	)
	monkeypatch.setattr(pipe, "apply_brief_css_tokens", lambda *_a, **_k: None)
	monkeypatch.setattr(pipe, "write_brief", lambda *_a, **_k: None)
	monkeypatch.setattr(pipe, "start_preview", lambda state, rows, app_dir=None: f"/projects/{pid}/preview/")
	monkeypatch.setattr(pipe, "save_checkpoint", lambda *_a, **_k: None)

	state = ProjectState(
		id=pid,
		prompt="vendor risk",
		phase="plan",
		status="draft",
		deploy_url=f"/projects/{pid}/preview/",
		app_config=AppConfig(title="Vendor Risk", subtitle="demo"),
		prime={"source": "template", "status": "ok"},
	)
	save_state(state)

	out = pipe.deepen_with_prime(pid)
	assert out.prime.get("source") == "prime"
	assert out.chat[-1].source == "prime"
	assert "Prime" not in out.chat[-1].content
	assert out.phase == "ready"


def test_request_cancel_idempotent_when_idle(tmp_path, monkeypatch):
	from simulacra.demo import jobs as jobs_mod
	from simulacra.demo.jobs import request_cancel
	from simulacra.demo.paths import RUNS_DIR
	from simulacra.demo.runs import ProjectState, save_state

	pid = "proj_cancel_idle"
	root = tmp_path / pid
	(root / "audit").mkdir(parents=True)
	monkeypatch.setenv("SIMULACRA_RUNS_DIR", str(tmp_path))
	# Point runs dir if module uses paths.RUNS_DIR — patch project_dir via state write
	from simulacra.demo import runs as runs_mod

	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	monkeypatch.setattr(jobs_mod, "load_state", runs_mod.load_state)
	monkeypatch.setattr(jobs_mod, "save_state", runs_mod.save_state)

	state = ProjectState(id=pid, prompt="x", phase="plan", status="planning")
	state.job = {**state.job, "status": "running", "id": "job_ghost"}
	save_state(state)

	with _lock:
		_jobs.pop(pid, None)

	out = request_cancel(pid)
	assert out["ok"] is True
	assert out.get("already_idle") is True
	healed = runs_mod.load_state(pid)
	assert healed.job["status"] == "idle"


def test_request_cancel_live_job():
	from simulacra.demo.jobs import request_cancel

	job = JobRecord(
		id="job_live",
		project_id="proj_live",
		kind="plan_ask",
		deadline=time.monotonic() + 60,
		max_steps=8,
	)
	with _lock:
		_jobs["proj_live"] = job
	try:
		# Avoid persist FileNotFound by stubbing
		from simulacra.demo import jobs as jobs_mod

		jobs_mod._persist = lambda *a, **k: None  # type: ignore
		out = request_cancel("proj_live")
		assert out["ok"] is True
		assert out.get("already_idle") is False
		assert job.cancel_requested is True
	finally:
		with _lock:
			_jobs.pop("proj_live", None)
