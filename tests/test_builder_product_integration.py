from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import json

import pytest

from simulacra.demo.builder_harness import run_app_builder
from simulacra.demo.operation_graph_builder import approved_graph_path, propose_operation_graph
from simulacra.demo.runs import AppConfig, ProjectState
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.harnesses import FakeHarness, TerminalStatus
from simulacra.operation_graph import (
	OperationGraphStore, UnapprovedRevisionError, canonical_json_bytes, deterministic_json, load_operation_graph,
)

ROOT = Path(__file__).parents[1]


def _approved_artifact(root: Path, *, tenant_id: str, project_id: str) -> tuple[Path, dict]:
	root.mkdir(parents=True, exist_ok=True)
	graph = load_operation_graph(ROOT / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = tenant_id
	graph["metadata"]["project_id"] = project_id
	store = OperationGraphStore(root, tenant_id=tenant_id, project_id=project_id)
	revision = store.create_revision(graph, expected_revision_hash=None)
	store.approve_revision(revision.revision_hash, actor_id="owner")
	path = root / "work/approved-operation-graph.json"
	path.parent.mkdir(exist_ok=True)
	path.write_text(deterministic_json(revision.graph, indent=2))
	return path, revision.graph


def test_neutral_template_survives_repeated_design_token_application(tmp_path: Path) -> None:
	from simulacra.demo.design_brief import apply_brief_css_tokens, default_brief

	app = tmp_path / "app"
	(app / "src").mkdir(parents=True)
	shutil.copyfile(Path(__file__).parents[1] / "templates/internal-app/src/styles.css", app / "src/styles.css")
	brief = default_brief(prompt="editorial workflow")
	assert apply_brief_css_tokens(app, brief)
	apply_brief_css_tokens(app, brief)
	css = (app / "src/styles.css").read_text()
	assert ".app" in css and ".workspace-grid" in css


def test_zero_row_gate_accepts_approved_graph_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	from simulacra.demo.gates import run_gates, write_manifest
	import simulacra.demo.gates as gates
	import simulacra.demo.runs as runs
	from simulacra.demo.duckdb_engine import rows_to_parquet

	root, state = _project(tmp_path)
	(root / "outputs").mkdir()
	(root / "audit").mkdir()
	monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
	monkeypatch.setattr(gates, "project_dir", lambda _project_id: root)
	rows_to_parquet([], root / "outputs/table.parquet")
	write_manifest(state, [], [])
	internal = root / ".simulacra/operation-graph/revisions"
	internal.mkdir(parents=True)
	(internal / "revision.json").write_text("{}")
	harness_state = root / ".cmul8/harness"
	harness_state.mkdir(parents=True)
	(harness_state / "sessions.json").write_text("{}")

	result = run_gates(state.id, min_rows=0)

	assert result["status"] == "pass"


def _project(tmp_path: Path) -> tuple[Path, ProjectState]:
	root = tmp_path / "proj_builder"
	for relative in ("inputs/data-room", "work", "app"):
		(root / relative).mkdir(parents=True, exist_ok=True)
	state = ProjectState(
		id="proj_builder",
		tenant_id="tenant_builder",
		prompt="Create a clean workspace for coordinating editorial work",
		goal="Coordinate editorial work",
		app_config=AppConfig(title="Editorial operations", subtitle="Plan and review work"),
	)
	return root, state


def test_architect_fallback_is_neutral_and_exact_approval_is_required(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	root, state = _project(tmp_path)
	import simulacra.demo.operation_graph_builder as builder
	import simulacra.demo.mutation_authorization as auth_mod

	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
	monkeypatch.setattr(builder, "project_dir", lambda _project_id: root)
	monkeypatch.setattr(builder, "save_state", lambda _state: None)
	CollaborationService(JsonCollaborationRepository(tmp_path / "control")).create_room(
		tenant_id=state.tenant_id, project_id=state.id, creator_id="owner", creator_role="owner",
	)

	graph = propose_operation_graph(state, actor_id="owner")

	serialized = str(graph).lower()
	assert graph["metadata"]["project_id"] == state.id
	assert graph["entities"][0]["name"] == "Record"
	assert "vendor" not in serialized and "onboarding" not in serialized
	with pytest.raises(UnapprovedRevisionError):
		approved_graph_path(state)
	store = OperationGraphStore(root, tenant_id=state.tenant_id, project_id=state.id)
	revision = store.current_revision()
	assert revision is not None
	store.approve_revision(revision.revision_hash, actor_id="owner_1")
	path = approved_graph_path(state)
	assert path is not None and path.is_file()
	assert revision.revision_hash == state.prime["operation_graph_revision"]


def test_architect_proposal_revalidates_room_authority_after_the_harness_turn(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	from dataclasses import replace
	from types import SimpleNamespace

	from simulacra.collaboration.models import Member, iso_now
	from simulacra.harnesses import TerminalStatus
	import simulacra.demo.operation_graph_builder as builder
	import simulacra.demo.mutation_authorization as auth_mod

	root, state = _project(tmp_path)
	repository = JsonCollaborationRepository(tmp_path / "control")
	service = CollaborationService(repository)
	room = service.create_room(tenant_id=state.tenant_id, project_id=state.id, creator_id="owner", creator_role="owner")
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
	monkeypatch.setattr(builder, "project_dir", lambda _project_id: root)
	monkeypatch.setattr(builder, "save_state", lambda _state: pytest.fail("demoted actor must not save proposal state"))

	class DemotingArchitect:
		async def run(self, _request):
			current = repository.get_room(state.tenant_id, state.id)
			repository.save_room(
				replace(current, members=[Member(actor_id="owner", role="viewer")], revision=current.revision + 1, updated_at=iso_now()),
				current.revision,
			)
			return SimpleNamespace(
				status=TerminalStatus.FAILED, structured_output=None,
				error={"code": "architect_unavailable"}, harness="custom", model_id="custom",
			)

	monkeypatch.setattr(builder, "create_harness", lambda *_args, **_kwargs: DemotingArchitect())
	with pytest.raises(PermissionError, match="Project Room owner or admin"):
		propose_operation_graph(state, actor_id="owner")
	assert OperationGraphStore(root, tenant_id=state.tenant_id, project_id=state.id).current_revision() is None
	assert room.members[0].role == "owner"


def test_architect_proposal_demotion_at_final_commit_leaves_no_revision_or_head(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	from contextlib import contextmanager
	from dataclasses import replace

	from simulacra.collaboration.models import Member, iso_now
	import simulacra.demo.operation_graph_builder as builder
	import simulacra.demo.mutation_authorization as auth_mod

	root, state = _project(tmp_path)
	repository = JsonCollaborationRepository(tmp_path / "control")
	service = CollaborationService(repository)
	service.create_room(tenant_id=state.tenant_id, project_id=state.id, creator_id="owner", creator_role="owner")
	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "control")
	monkeypatch.setattr(builder, "project_dir", lambda _project_id: root)
	monkeypatch.setattr(builder, "save_state", lambda _state: pytest.fail("demoted actor must not save proposal state"))
	original_commit = builder.room_mutation_commit

	@contextmanager
	def demote_at_commit(project_id, *, tenant_id, actor_id):
		current = repository.get_room(tenant_id, project_id)
		repository.save_room(
			replace(current, members=[Member(actor_id=actor_id, role="viewer")], revision=current.revision + 1, updated_at=iso_now()),
			current.revision,
		)
		with original_commit(project_id, tenant_id=tenant_id, actor_id=actor_id):
			yield

	monkeypatch.setattr(builder, "room_mutation_commit", demote_at_commit)
	with pytest.raises(PermissionError, match="Project Room owner or admin"):
		propose_operation_graph(state, actor_id="owner")
	store = OperationGraphStore(root, tenant_id=state.tenant_id, project_id=state.id)
	assert store.current_revision() is None
	assert not (root / ".simulacra/operation-graph/head.json").exists()
	assert "operation_graph_revision" not in state.prime


def test_application_builder_rejects_missing_approved_graph(tmp_path: Path) -> None:
	app = tmp_path / "app"
	app.mkdir()
	with pytest.raises(PermissionError, match="approved Operation Graph"):
		run_app_builder(
			app,
			"Build the application",
			project_id="proj_builder",
			row_count=0,
			kind="build_run",
			operation_graph_path=None,
		)


def test_approved_graph_staging_replaces_a_file_symlink_without_touching_external_target(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	root, state = _project(tmp_path)
	_approved_artifact(root, tenant_id=state.tenant_id, project_id=state.id)
	external = tmp_path / "outside-approved-graph.json"
	external.write_text("external sentinel\n", encoding="utf-8")
	asset = root / "work" / "approved-operation-graph.json"
	asset.unlink()
	asset.symlink_to(external)
	import simulacra.demo.operation_graph_builder as graph_builder
	monkeypatch.setattr(graph_builder, "project_dir", lambda _project_id: root)

	path = approved_graph_path(state)

	assert path == asset and not asset.is_symlink()
	assert external.read_text(encoding="utf-8") == "external sentinel\n"
	assert json.loads(asset.read_text(encoding="utf-8"))["metadata"]["project_id"] == state.id


def test_approved_graph_staging_rejects_a_symlinked_work_directory_without_external_mutation(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	root, state = _project(tmp_path)
	_approved_artifact(root, tenant_id=state.tenant_id, project_id=state.id)
	external_work = tmp_path / "outside-work"
	external_work.mkdir()
	external_asset = external_work / "approved-operation-graph.json"
	external_asset.write_text("external sentinel\n", encoding="utf-8")
	work = root / "work"
	(work / "approved-operation-graph.json").unlink()
	work.rmdir()
	work.symlink_to(external_work, target_is_directory=True)
	import simulacra.demo.operation_graph_builder as graph_builder
	monkeypatch.setattr(graph_builder, "project_dir", lambda _project_id: root)

	with pytest.raises(PermissionError, match="symlink"):
		approved_graph_path(state)

	assert external_asset.read_text(encoding="utf-8") == "external sentinel\n"


def test_builder_refuses_a_legacy_unsafe_graph_without_publishing_its_secret(tmp_path: Path) -> None:
	root = tmp_path / "proj_builder"
	app = root / "app"
	app.mkdir(parents=True)
	graph = load_operation_graph(ROOT / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_builder"
	graph["metadata"]["project_id"] = "proj_builder"
	sentinel = "sk-legacy-builder-secret-sentinel"
	graph["connectors"][0]["configuration"] = {"nested": {"accessToken": sentinel}}
	store = OperationGraphStore(root, tenant_id="tenant_builder", project_id="proj_builder")
	revision_hash = hashlib.sha256(canonical_json_bytes(graph)).hexdigest()
	store._write_immutable(store._revisions / f"{revision_hash}.json", {
		"schema_version": "cmul8.operation-graph.store.v0",
		"tenant_id": "tenant_builder",
		"project_id": "proj_builder",
		"revision": 1,
		"revision_hash": revision_hash,
		"created_at": "2026-08-23T00:00:00Z",
		"updated_at": "2026-08-23T00:00:00Z",
		"graph": graph,
	})
	store._atomic_write(store._root / "head.json", {
		"schema_version": "cmul8.operation-graph.store.v0",
		"tenant_id": "tenant_builder",
		"project_id": "proj_builder",
		"revision": 1,
		"revision_hash": revision_hash,
		"created_at": "2026-08-23T00:00:00Z",
		"updated_at": "2026-08-23T00:00:00Z",
	})
	path = root / "work" / "approved-operation-graph.json"
	path.parent.mkdir()
	path.write_text(deterministic_json(graph, indent=2), encoding="utf-8")

	with pytest.raises(PermissionError) as raised:
		run_app_builder(app, "Build", project_id="proj_builder", row_count=0, kind="build_run", operation_graph_path=path)

	assert sentinel not in str(raised.value)
	asset = app / "public" / "operation-graph.json"
	assert not asset.exists()
	assert sentinel not in "".join(file.read_text(encoding="utf-8") for file in app.rglob("*") if file.is_file())


def test_fake_builder_stays_inside_app_and_publishes_graph_asset(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	root = tmp_path / "proj_builder"
	app = root / "app"
	app.mkdir(parents=True)
	graph, approved = _approved_artifact(root, tenant_id="tenant_builder", project_id="proj_builder")
	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")

	result = run_app_builder(
		app, "Build the application", project_id="proj_builder", row_count=0,
		kind="build_run", operation_graph_path=graph,
	)

	assert result["ok"] is True
	assert (app / "fake-artifact.txt").is_file()
	assert (app / "public/operation-graph.json").read_bytes() == canonical_json_bytes(approved)


def test_builder_restores_the_exact_approved_graph_after_harness_tampering(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	class GraphTamperingHarness(FakeHarness):
		async def _run_provider(self, request, session):  # type: ignore[no-untyped-def]
			del session
			asset = request.write_paths[0] / "public" / "operation-graph.json"
			asset.write_text('{"metadata":{"name":"unapproved replacement"}}\n', encoding="utf-8")
			artifact = request.write_paths[0] / "fake-artifact.txt"
			artifact.write_text("builder output\n", encoding="utf-8")
			return {
				"status": TerminalStatus.SUCCEEDED,
				"changed_files": [asset, artifact],
				"events": [{"type": "tamper", "status": "completed"}],
				"steps": 1,
			}

	root = tmp_path / "proj_builder"
	app = root / "app"
	app.mkdir(parents=True)
	path, approved = _approved_artifact(root, tenant_id="tenant_builder", project_id="proj_builder")
	import simulacra.demo.builder_harness as builder_harness

	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")
	monkeypatch.setattr(builder_harness, "create_harness", lambda _config, **_adapters: GraphTamperingHarness())

	result = run_app_builder(
		app, "Build", project_id="proj_builder", row_count=0,
		kind="build_run", operation_graph_path=path,
	)

	assert result["ok"] is True
	assert (app / "public/operation-graph.json").read_bytes() == canonical_json_bytes(approved)


def test_builder_replaces_an_asset_symlink_without_touching_its_external_target(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	root = tmp_path / "proj_builder"
	app = root / "app"
	(app / "public").mkdir(parents=True)
	path, approved = _approved_artifact(root, tenant_id="tenant_builder", project_id="proj_builder")
	external = tmp_path / "outside-graph.json"
	external.write_text("external sentinel\n", encoding="utf-8")
	asset = app / "public/operation-graph.json"
	asset.symlink_to(external)
	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")

	result = run_app_builder(app, "Build", project_id="proj_builder", row_count=0, kind="build_run", operation_graph_path=path)

	assert result["ok"] is True
	assert external.read_text(encoding="utf-8") == "external sentinel\n"
	assert not asset.is_symlink()
	assert asset.read_bytes() == canonical_json_bytes(approved)


def test_builder_rejects_a_builder_time_public_directory_symlink_swap_without_external_mutation(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
	class PublicDirectorySwapHarness(FakeHarness):
		async def _run_provider(self, request, session):  # type: ignore[no-untyped-def]
			del session
			asset = request.write_paths[0] / "public" / "operation-graph.json"
			asset.unlink()
			asset.parent.rmdir()
			asset.parent.symlink_to(external_directory, target_is_directory=True)
			artifact = request.write_paths[0] / "fake-artifact.txt"
			artifact.write_text("builder output\n", encoding="utf-8")
			return {"status": TerminalStatus.SUCCEEDED, "changed_files": [artifact], "steps": 1}

	root = tmp_path / "proj_builder"
	app = root / "app"
	app.mkdir(parents=True)
	path, _approved = _approved_artifact(root, tenant_id="tenant_builder", project_id="proj_builder")
	external_directory = tmp_path / "outside-public"
	external_directory.mkdir()
	external_graph = external_directory / "operation-graph.json"
	external_graph.write_text("external sentinel\n", encoding="utf-8")
	import simulacra.demo.builder_harness as builder_harness

	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")
	monkeypatch.setattr(builder_harness, "create_harness", lambda _config, **_adapters: PublicDirectorySwapHarness())

	with pytest.raises(PermissionError, match="public directory"):
		run_app_builder(app, "Build", project_id="proj_builder", row_count=0, kind="build_run", operation_graph_path=path)

	assert external_graph.read_text(encoding="utf-8") == "external sentinel\n"


def test_builder_rejects_tampered_or_project_mismatched_graph(tmp_path: Path) -> None:
	root = tmp_path / "proj_builder"
	app = root / "app"
	app.mkdir(parents=True)
	path, _graph = _approved_artifact(root, tenant_id="tenant_builder", project_id="proj_builder")
	tampered = json.loads(path.read_text())
	tampered["metadata"]["name"] = "Tampered graph"
	path.write_text(deterministic_json(tampered, indent=2))
	with pytest.raises(PermissionError, match="changed|current exact"):
		run_app_builder(app, "Build", project_id="proj_builder", row_count=0, kind="build_run", operation_graph_path=path)

	other_path, _graph = _approved_artifact(tmp_path / "other", tenant_id="tenant_builder", project_id="other_project")
	path = root / "work/mismatched-operation-graph.json"
	path.write_text(other_path.read_text())
	with pytest.raises(PermissionError, match="does not match"):
		run_app_builder(app, "Build", project_id="proj_builder", row_count=0, kind="build_run", operation_graph_path=path)


def test_builder_rejects_an_older_approved_graph_after_the_head_changes(tmp_path: Path) -> None:
	root = tmp_path / "proj_builder"
	app = root / "app"
	app.mkdir(parents=True)
	path, first_graph = _approved_artifact(root, tenant_id="tenant_builder", project_id="proj_builder")
	store = OperationGraphStore(root, tenant_id="tenant_builder", project_id="proj_builder")
	first = store.current_revision()
	assert first is not None
	second_graph = json.loads(json.dumps(first_graph))
	second_graph["metadata"]["version"] = 1
	second = store.create_revision(second_graph, expected_revision_hash=first.revision_hash)
	store.approve_revision(second.revision_hash, actor_id="owner")

	with pytest.raises(PermissionError, match="current exact"):
		run_app_builder(app, "Build", project_id="proj_builder", row_count=0, kind="build_run", operation_graph_path=path)
