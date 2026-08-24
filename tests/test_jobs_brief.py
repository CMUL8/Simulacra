"""Unit tests for design brief + job bounds (no Prime required)."""

from __future__ import annotations

import time
import json
from dataclasses import replace
from types import SimpleNamespace

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
	from simulacra.demo import mutation_authorization as auth_mod
	from simulacra.demo import runs as runs_mod
	from simulacra.demo.runs import AppConfig, ProjectState, save_state
	from simulacra.collaboration import CollaborationService, JsonCollaborationRepository

	pid = "proj_boot"
	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
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
	called_builder = {"n": 0}

	def _builder(*_a, **_k):
		called_builder["n"] += 1
		return {
			"used": True,
			"ok": True,
			"source": "prime",
			"layout_customized": True,
			"style_only": False,
		}

	monkeypatch.setattr(pipe, "run_app_builder", _builder)
	monkeypatch.setattr(pipe, "approved_graph_path", lambda *_a, **_k: tmp_path / "approved.json")
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
	CollaborationService(JsonCollaborationRepository(tmp_path / "control")).create_room(
		tenant_id=state.tenant_id, project_id=pid, creator_id="owner", creator_role="owner",
	)

	out = pipe.bootstrap_project(runs_mod.load_state(pid), actor_id="owner")
	assert called_builder["n"] == 1
	assert out.phase == "ready"
	assert out.status == "ready"
	assert out.deploy_url == f"/projects/{pid}/preview/"
	assert out.prime.get("source") == "prime"
	assert out.chat[-1].source == "prime"
	assert "is ready in Preview" in out.chat[-1].content or "Preview is ready" in out.chat[-1].content
	assert "Prime" not in out.chat[-1].content
	assert "Build app" not in out.chat[-1].content


def test_deepen_calls_prime_when_preview_exists(tmp_path, monkeypatch):
	from simulacra.demo import pipeline as pipe
	from simulacra.demo import mutation_authorization as auth_mod
	from simulacra.demo import runs as runs_mod
	from simulacra.demo.runs import AppConfig, ProjectState, save_state
	from simulacra.collaboration import CollaborationService, JsonCollaborationRepository

	pid = "proj_deep"
	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
	for d in ("inputs/data-room", "outputs", "work", "app", "audit"):
		(tmp_path / pid / d).mkdir(parents=True)
	(tmp_path / pid / "outputs" / "table.parquet").write_bytes(b"")

	monkeypatch.setattr(pipe, "_load_rows", lambda *_a, **_k: [{"vendor": "A", "risk_level": "low"}])
	monkeypatch.setattr(
		pipe,
		"run_app_builder",
		lambda *_a, **_k: {"used": True, "ok": True, "source": "prime", "session_id": "s1", "model": "m"},
	)
	monkeypatch.setattr(pipe, "approved_graph_path", lambda *_a, **_k: tmp_path / "approved.json")
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
	CollaborationService(JsonCollaborationRepository(tmp_path / "control")).create_room(
		tenant_id=state.tenant_id, project_id=pid, creator_id="owner", creator_role="owner",
	)

	out = pipe.deepen_with_prime(pid, actor_id="owner")
	assert out.prime.get("source") == "prime"
	assert out.chat[-1].source == "prime"
	assert "Prime" not in out.chat[-1].content
	assert out.phase == "ready"


def test_background_chat_build_request_requires_a_current_room_owner_or_admin(tmp_path, monkeypatch):
	from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
	from simulacra.demo import plan as plan_mod
	from simulacra.demo import mutation_authorization as auth_mod
	from simulacra.demo import runs as runs_mod
	from simulacra.demo.prime_hook import PrimeBuildMeta, PrimeChatTurn
	from simulacra.demo.runs import ProjectState, save_state
	from simulacra.demo import observe as observe_mod

	pid = "proj_chat_authority"
	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
	for directory in ("inputs/data-room", "work", "audit", "app"):
		(tmp_path / pid / directory).mkdir(parents=True)
	marker = tmp_path / pid / "app" / "source.txt"
	marker.write_text("unchanged")
	state = ProjectState(id=pid, prompt="Build a controlled project", phase="plan", status="planning")
	save_state(state)
	service = CollaborationService(JsonCollaborationRepository(tmp_path / "control"))
	room = service.create_room(tenant_id=state.tenant_id, project_id=pid, creator_id="owner", creator_role="owner")
	service.add_member(
		tenant_id=state.tenant_id, project_id=pid, actor_id="owner", member_id="viewer",
		role="viewer", expected_revision=room.revision,
	)
	monkeypatch.setattr(
		plan_mod, "run_chat_builder",
		lambda *_args, **_kwargs: PrimeChatTurn(
			reply="I am ready to build.", request="build", brief="build it",
			meta=PrimeBuildMeta(used=True, source="test"),
		),
	)
	prewarmed: list[str] = []
	monkeypatch.setattr(observe_mod, "prewarm_for_build", lambda project_id: prewarmed.append(project_id))

	denied = plan_mod._agent_chat_turn(pid, message="Please build", open_turn=False, actor_id="viewer")
	assert marker.read_text() == "unchanged"
	assert prewarmed == []
	assert "request" not in denied.prime
	assert denied.chat[-2].content == "I am ready to build."
	assert "owner or admin" in denied.chat[-1].content

	plan_mod._agent_chat_turn(pid, message="Please build", open_turn=False, actor_id="owner")
	assert prewarmed == [pid]
	assert marker.read_text() == "unchanged"


def test_read_only_chat_harness_cannot_promote_or_write_for_viewer_or_nonmember(tmp_path, monkeypatch):
	from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
	from simulacra.demo import builder_harness, mutation_authorization as auth_mod, plan as plan_mod, runs as runs_mod
	from simulacra.demo.runs import AppConfig, ProjectState, save_state
	from simulacra.harnesses import TerminalStatus

	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
	requests = []

	class WriteCapableHarness:
		async def run(self, request):
			requests.append(request)
			if request.write_paths:
				target = request.write_paths[0] / "harness-write.txt"
				target.parent.mkdir(parents=True, exist_ok=True)
				target.write_text("unexpected")
			return SimpleNamespace(
				response=json.dumps({
					"reply": "I can build this.", "request": "build", "brief": "rewrite the app",
					"title": "Changed by harness", "subtitle": "should not persist",
				}),
				structured_output=None, error=None, session_id="readonly-session", model_id="custom",
				harness="custom", status=TerminalStatus.SUCCEEDED,
			)

	monkeypatch.setattr(builder_harness, "create_harness", lambda *_args, **_kwargs: WriteCapableHarness())

	for actor_id in ("viewer", "nonmember"):
		pid = f"readonly_{actor_id}"
		for directory in ("inputs/data-room", "work", "app", "audit"):
			(tmp_path / pid / directory).mkdir(parents=True)
		data_file = tmp_path / pid / "inputs/data-room/source.md"
		app_file = tmp_path / pid / "app/source.txt"
		graph_file = tmp_path / pid / "operation-graph-marker.json"
		data_file.write_text("source unchanged")
		app_file.write_text("app unchanged")
		graph_file.write_text("graph unchanged")
		state = ProjectState(
			id=pid, prompt="Original prompt", phase="plan", status="planning",
			app_config=AppConfig(title="Original title", subtitle="Original subtitle"),
		)
		save_state(state)
		before_brief = json.dumps(runs_mod.load_state(pid).design_brief, sort_keys=True)
		service = CollaborationService(JsonCollaborationRepository(tmp_path / "control"))
		room = service.create_room(tenant_id=state.tenant_id, project_id=pid, creator_id="owner", creator_role="owner")
		if actor_id == "viewer":
			service.add_member(
				tenant_id=state.tenant_id, project_id=pid, actor_id="owner", member_id="viewer",
				role="viewer", expected_revision=room.revision,
			)

		def start_inline(_project_id, _kind, *, label, target, **_kwargs):
			target(None)
			return SimpleNamespace(id="chat-job")

		monkeypatch.setattr(plan_mod, "start_job", start_inline)
		plan_mod.start_agent_chat(pid, "Please build a new app", actor_id=actor_id)
		after = runs_mod.load_state(pid)
		assert after.prompt == "Original prompt"
		assert json.dumps(after.design_brief, sort_keys=True) == before_brief
		assert after.app_config.title == "Original title"
		assert "request" not in after.prime and "brief" not in after.prime
		assert "owner or admin" in after.chat[-1].content
		assert data_file.read_text() == "source unchanged"
		assert app_file.read_text() == "app unchanged"
		assert graph_file.read_text() == "graph unchanged"
		assert not (tmp_path / pid / "work/research").exists()

	assert all(request.write_paths == () for request in requests)


def test_real_read_only_chat_has_no_harness_or_research_session_artifacts(tmp_path, monkeypatch):
	from simulacra.demo import builder_harness, runs as runs_mod
	from simulacra.demo.runs import ProjectState, save_state

	pid = "ephemeral_chat"
	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")
	for directory in ("inputs/data-room", "work", "app", "audit"):
		(tmp_path / pid / directory).mkdir(parents=True)
	state = ProjectState(id=pid, prompt="Read this project without changing it", phase="plan")
	save_state(state)
	before_prime = dict(runs_mod.load_state(pid).prime)

	turn = builder_harness.run_chat_builder(
		runs_mod.load_state(pid), message="What is here?", open_turn=False, read_only=True,
	)

	assert turn.meta.used and turn.meta.source == "fake"
	assert runs_mod.load_state(pid).prime == before_prime
	assert not (tmp_path / pid / ".cmul8/harness/sessions.json").exists()
	assert not (tmp_path / pid / "work/research").exists()
	assert not (tmp_path / pid / "work/prime-session").exists()


def test_read_only_chat_job_lifecycle_preserves_prime_metadata_for_viewer_and_nonmember(tmp_path, monkeypatch):
	from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
	from simulacra.demo import mutation_authorization as auth_mod, plan as plan_mod, runs as runs_mod
	from simulacra.demo.jobs import get_job
	from simulacra.demo.runs import ProjectState, save_state

	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")

	for actor_id in ("viewer", "nonmember"):
		pid = f"readonly_job_{actor_id}"
		for directory in ("inputs/data-room", "work", "app", "audit"):
			(tmp_path / pid / directory).mkdir(parents=True)
		data_file = tmp_path / pid / "inputs/data-room/source.md"
		app_file = tmp_path / pid / "app/source.txt"
		graph_file = tmp_path / pid / "graph-marker.json"
		data_file.write_text("source unchanged")
		app_file.write_text("app unchanged")
		graph_file.write_text("graph unchanged")
		state = ProjectState(
			id=pid, prompt="Original prompt", phase="plan",
			prime={"status": "prior", "session_id": "prior-session", "request": "await_user", "brief": "prior brief"},
		)
		save_state(state)
		before_prime = json.loads(json.dumps(runs_mod.load_state(pid).prime))
		service = CollaborationService(JsonCollaborationRepository(tmp_path / "control"))
		room = service.create_room(tenant_id=state.tenant_id, project_id=pid, creator_id="owner", creator_role="owner")
		if actor_id == "viewer":
			service.add_member(
				tenant_id=state.tenant_id, project_id=pid, actor_id="owner", member_id="viewer",
				role="viewer", expected_revision=room.revision,
			)

		plan_mod.start_agent_chat(pid, "Please build a new app", actor_id=actor_id)
		deadline = time.monotonic() + 5
		while get_job(pid) is not None and time.monotonic() < deadline:
			time.sleep(0.02)
		assert get_job(pid) is None
		after = runs_mod.load_state(pid)
		assert after.prime == before_prime
		assert data_file.read_text() == "source unchanged"
		assert app_file.read_text() == "app unchanged"
		assert graph_file.read_text() == "graph unchanged"
		assert not (tmp_path / pid / ".cmul8/harness/sessions.json").exists()
		assert not (tmp_path / pid / "work/research").exists()

	# Authorized chat retains the generic agent-job metadata projection.
	owner_pid = "owner_job_metadata"
	for directory in ("inputs/data-room", "work", "app", "audit"):
		(tmp_path / owner_pid / directory).mkdir(parents=True)
	owner_state = ProjectState(id=owner_pid, prompt="Original prompt", phase="plan")
	save_state(owner_state)
	CollaborationService(JsonCollaborationRepository(tmp_path / "control")).create_room(
		tenant_id=owner_state.tenant_id, project_id=owner_pid, creator_id="owner", creator_role="owner",
	)
	plan_mod.start_agent_chat(owner_pid, "Please discuss this", actor_id="owner")
	deadline = time.monotonic() + 5
	while get_job(owner_pid) is not None and time.monotonic() < deadline:
		time.sleep(0.02)
	assert get_job(owner_pid) is None
	owner_after = runs_mod.load_state(owner_pid)
	assert owner_after.prime["status"] == "ok"
	assert "duration_ms" in owner_after.prime


def test_suppressed_agent_metadata_survives_failed_and_cancelled_job_bookkeeping(tmp_path, monkeypatch):
	from simulacra.demo import runs as runs_mod
	from simulacra.demo.jobs import JobCancelled, get_job, start_job
	from simulacra.demo.runs import ProjectState, save_state

	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	for suffix, target in (
		("failed", lambda _job: (_ for _ in ()).throw(RuntimeError("chat failure"))),
		("cancelled", lambda _job: (_ for _ in ()).throw(JobCancelled("cancelled by user"))),
	):
		pid = f"suppressed_{suffix}"
		(tmp_path / pid / "audit").mkdir(parents=True)
		state = ProjectState(id=pid, prompt="No mutation", prime={"status": "prior", "request": "await_user"})
		save_state(state)
		before_prime = json.loads(json.dumps(runs_mod.load_state(pid).prime))
		start_job(
			pid, "agent_chat", label="Read-only chat", target=target,
			persist_agent_metadata=False,
		)
		deadline = time.monotonic() + 5
		while get_job(pid) is not None and time.monotonic() < deadline:
			time.sleep(0.02)
		assert get_job(pid) is None
		assert runs_mod.load_state(pid).prime == before_prime


def test_demotion_during_chat_turn_drops_structured_output_before_promotion(tmp_path, monkeypatch):
	from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
	from simulacra.collaboration.models import Member, iso_now
	from simulacra.demo import builder_harness, mutation_authorization as auth_mod, plan as plan_mod, runs as runs_mod
	from simulacra.demo.runs import AppConfig, ProjectState, save_state
	from simulacra.harnesses import TerminalStatus

	pid = "demoted_chat"
	monkeypatch.setattr(runs_mod, "RUNS_DIR", tmp_path)
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
	for directory in ("inputs/data-room", "work", "app", "audit"):
		(tmp_path / pid / directory).mkdir(parents=True)
	app_file = tmp_path / pid / "app/source.txt"
	graph_file = tmp_path / pid / "graph-marker.json"
	app_file.write_text("app unchanged")
	graph_file.write_text("graph unchanged")
	state = ProjectState(
		id=pid, prompt="Original prompt", phase="plan", status="planning",
		app_config=AppConfig(title="Original title", subtitle="Original subtitle"),
	)
	save_state(state)
	repository = JsonCollaborationRepository(tmp_path / "control")
	service = CollaborationService(repository)
	room = service.create_room(tenant_id=state.tenant_id, project_id=pid, creator_id="owner", creator_role="owner")

	class DemotingHarness:
		async def run(self, _request):
			current = repository.get_room(state.tenant_id, pid)
			repository.save_room(
				replace(current, members=[Member(actor_id="owner", role="viewer")], revision=current.revision + 1, updated_at=iso_now()),
				current.revision,
			)
			return SimpleNamespace(
				response=json.dumps({"reply": "I can build this.", "request": "build", "brief": "rewrite", "title": "Changed"}),
				structured_output=None, error=None, session_id="demoted-session", model_id="custom",
				harness="custom", status=TerminalStatus.SUCCEEDED,
			)

	monkeypatch.setattr(builder_harness, "create_harness", lambda *_args, **_kwargs: DemotingHarness())
	result = plan_mod._agent_chat_turn(pid, message="Please build", open_turn=False, actor_id="owner")
	assert result.app_config.title == "Original title"
	assert "request" not in result.prime and "brief" not in result.prime
	assert result.chat[-2].content == "I can build this."
	assert "owner or admin" in result.chat[-1].content
	assert app_file.read_text() == "app unchanged"
	assert graph_file.read_text() == "graph unchanged"


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
