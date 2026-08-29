from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
from pathlib import Path

import pytest

from simulacra.operation_graph import (
	OperationGraphStore,
	RevisionConflictError,
	canonical_json_bytes,
	load_operation_graph,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "schemas" / "operation-graph.v0.yaml"


def _graph(*, version: int = 0) -> dict:
	graph = load_operation_graph(EXAMPLE)
	graph["metadata"]["version"] = version
	return graph


def _finalize_after_barrier(
	project_root: str,
	revision_hash: str,
	barrier: multiprocessing.synchronize.Barrier,
	results: multiprocessing.queues.Queue,
) -> None:
	store = OperationGraphStore(project_root, tenant_id="tenant_acme", project_id="project_support")
	barrier.wait(timeout=10)
	try:
		revision = store.finalize_exact_revision_head(
			tenant_id="tenant_acme",
			project_id="project_support",
			revision_hash=revision_hash,
			canonical_graph_hash=revision_hash,
		)
		results.put(("ok", revision.revision_hash))
	except Exception as exc:  # pragma: no cover - returned to the parent for assertion
		results.put(("error", type(exc).__name__))


def test_revision_created_without_head_recovers_to_the_same_exact_revision(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_immutable_revision(_graph())

	assert store.current_revision() is None
	assert store.list_revisions() == [revision]

	first = store.finalize_exact_revision_head(
		tenant_id="tenant_acme",
		project_id="project_support",
		revision_hash=revision.revision_hash,
		canonical_graph_hash=revision.revision_hash,
	)
	second = store.finalize_exact_revision_head(
		tenant_id="tenant_acme",
		project_id="project_support",
		revision_hash=revision.revision_hash,
		canonical_graph_hash=revision.revision_hash,
	)

	assert first == second == revision
	assert store.current_revision() == revision
	assert store.list_revisions() == [revision]


def test_two_process_finalizers_converge_on_one_head(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_immutable_revision(_graph())
	context = multiprocessing.get_context("spawn")
	barrier = context.Barrier(2)
	results = context.Queue()
	processes = [
		context.Process(
			target=_finalize_after_barrier,
			args=(str(tmp_path), revision.revision_hash, barrier, results),
		)
		for _ in range(2)
	]
	for process in processes:
		process.start()
	for process in processes:
		process.join(timeout=15)
		assert process.exitcode == 0

	assert sorted(results.get(timeout=2) for _ in processes) == [
		("ok", revision.revision_hash),
		("ok", revision.revision_hash),
	]
	assert store.current_revision() == revision
	assert store.list_revisions() == [revision]


@pytest.mark.parametrize(
	("tenant_id", "project_id", "canonical_hash"),
	[
		("tenant_other", "project_support", None),
		("tenant_acme", "project_other", None),
		("tenant_acme", "project_support", "0" * 64),
	],
)
def test_scope_or_hash_mismatch_does_not_publish_a_head(
	tmp_path: Path,
	tenant_id: str,
	project_id: str,
	canonical_hash: str | None,
):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_immutable_revision(_graph())
	before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

	with pytest.raises(RevisionConflictError):
		store.finalize_exact_revision_head(
			tenant_id=tenant_id,
			project_id=project_id,
			revision_hash=revision.revision_hash,
			canonical_graph_hash=canonical_hash or revision.revision_hash,
		)

	assert sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*")) == before
	assert store.current_revision() is None


def test_existing_different_head_is_a_stable_conflict_and_is_not_replaced(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	first = store.create_revision(_graph(), expected_revision_hash=None)
	second_graph = _graph(version=1)
	second = store.create_immutable_revision(second_graph)
	head_path = tmp_path / ".simulacra" / "operation-graph" / "head.json"
	before = head_path.read_bytes()

	with pytest.raises(RevisionConflictError, match="already names another"):
		store.finalize_exact_revision_head(
			tenant_id="tenant_acme",
			project_id="project_support",
			revision_hash=second.revision_hash,
			canonical_graph_hash=hashlib.sha256(canonical_json_bytes(second_graph)).hexdigest(),
		)

	assert head_path.read_bytes() == before
	assert store.current_revision() == first


def test_same_hash_malformed_head_conflicts_instead_of_claiming_success(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_immutable_revision(_graph())
	head_path = tmp_path / ".simulacra" / "operation-graph" / "head.json"
	head_path.write_text(json.dumps({
		"schema_version": "wrong",
		"tenant_id": "tenant_acme",
		"project_id": "project_support",
		"revision": revision.revision + 1,
		"revision_hash": revision.revision_hash,
		"created_at": revision.created_at,
		"updated_at": revision.updated_at,
	}))
	before = head_path.read_bytes()

	with pytest.raises(RevisionConflictError, match="metadata"):
		store.finalize_exact_revision_head(
			tenant_id="tenant_acme",
			project_id="project_support",
			revision_hash=revision.revision_hash,
			canonical_graph_hash=revision.revision_hash,
		)

	assert head_path.read_bytes() == before


def test_exact_head_reader_rejects_malformed_head_metadata(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_immutable_revision(_graph())
	store.finalize_exact_revision_head(
		tenant_id="tenant_acme",
		project_id="project_support",
		revision_hash=revision.revision_hash,
		canonical_graph_hash=revision.revision_hash,
	)
	head_path = tmp_path / ".simulacra" / "operation-graph" / "head.json"
	head = json.loads(head_path.read_text())
	head["revision"] = revision.revision + 1
	head_path.write_text(json.dumps(head))

	with pytest.raises(RevisionConflictError, match="metadata"):
		store.require_exact_current_revision_head(revision.revision_hash)


@pytest.mark.parametrize(
	("field", "value"),
	[
		("schema_version", "wrong"),
		("revision", 0),
		("revision", True),
		("created_at", ""),
		("updated_at", None),
	],
)
def test_revision_load_rejects_malformed_metadata_even_when_graph_hash_matches(
	tmp_path: Path,
	field: str,
	value: object,
):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_immutable_revision(_graph())
	revision_path = tmp_path / ".simulacra" / "operation-graph" / "revisions" / f"{revision.revision_hash}.json"
	record = json.loads(revision_path.read_text())
	record[field] = value
	revision_path.write_text(json.dumps(record))

	with pytest.raises(ValueError, match="metadata"):
		store.load_revision(revision.revision_hash)


def test_parent_directory_fsync_failure_cannot_return_revision_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")

	def fail_fsync(_directory: Path) -> None:
		raise OSError("injected directory durability failure")

	monkeypatch.setattr(store, "_fsync_directory", fail_fsync)
	with pytest.raises(OSError, match="durability"):
		store.create_immutable_revision(copy.deepcopy(_graph()))


def test_parent_directory_fsync_failure_cannot_return_head_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_immutable_revision(_graph())

	def fail_fsync(_directory: Path) -> None:
		raise OSError("injected directory durability failure")

	monkeypatch.setattr(store, "_fsync_directory", fail_fsync)
	with pytest.raises(OSError, match="durability"):
		store.finalize_exact_revision_head(
			tenant_id="tenant_acme",
			project_id="project_support",
			revision_hash=revision.revision_hash,
			canonical_graph_hash=revision.revision_hash,
		)


def test_retry_reestablishes_revision_directory_durability_after_link_succeeded(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	original = store._fsync_directory

	def fail_revision_sync(directory: Path) -> None:
		if directory == store._revisions:
			raise OSError("injected revision directory sync failure")
		original(directory)

	monkeypatch.setattr(store, "_fsync_directory", fail_revision_sync)
	with pytest.raises(OSError, match="revision directory"):
		store.create_immutable_revision(_graph())
	assert len(list(store._revisions.glob("*.json"))) == 1

	monkeypatch.setattr(store, "_fsync_directory", original)
	revision = store.create_immutable_revision(_graph())
	assert store.list_revisions() == [revision]


def test_retry_reestablishes_head_directory_durability_after_replace_succeeded(
	tmp_path: Path,
	monkeypatch: pytest.MonkeyPatch,
):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_immutable_revision(_graph())
	original = store._fsync_directory

	def fail_head_sync(directory: Path) -> None:
		if directory == store._root:
			raise OSError("injected head directory sync failure")
		original(directory)

	monkeypatch.setattr(store, "_fsync_directory", fail_head_sync)
	with pytest.raises(OSError, match="head directory"):
		store.finalize_exact_revision_head(
			tenant_id="tenant_acme",
			project_id="project_support",
			revision_hash=revision.revision_hash,
			canonical_graph_hash=revision.revision_hash,
		)
	assert (store._root / "head.json").is_file()

	monkeypatch.setattr(store, "_fsync_directory", original)
	assert store.finalize_exact_revision_head(
		tenant_id="tenant_acme",
		project_id="project_support",
		revision_hash=revision.revision_hash,
		canonical_graph_hash=revision.revision_hash,
	) == revision
