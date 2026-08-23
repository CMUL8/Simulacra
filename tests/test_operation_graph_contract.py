from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
from pathlib import Path

import pytest

from simulacra.operation_graph import (
	GraphParseError,
	GraphValidationError,
	METADATA_FIELDS,
	OperationGraphStore,
	RevisionConflictError,
	UnapprovedRevisionError,
	business_summary,
	canonical_json_bytes,
	deterministic_json,
	load_operation_graph,
	migrate_manifest_v0,
	parse_operation_graph,
	structural_diff,
	validate_operation_graph,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "schemas" / "operation-graph.v0.yaml"
LEGACY = ROOT / "tests" / "fixtures" / "operation_graph" / "manifest.v0.json"


def example_graph() -> dict:
	return load_operation_graph(EXAMPLE)


def test_schema_and_yaml_example_are_canonical_and_valid():
	schema = json.loads((ROOT / "schemas" / "operation-graph.v0.json").read_text())
	graph = validate_operation_graph(example_graph())
	assert schema["$id"] == "cmul8.operation-graph.v0"
	assert schema["required"] == ["metadata", "entities", "views", "workflows", "agents", "automations", "connectors", "permissions", "approval_rules", "schedules"]
	assert graph["metadata"]["schema_id"] == schema["$id"]
	assert parse_operation_graph(deterministic_json(graph), syntax="json") == graph


def test_schema_and_runtime_metadata_contract_accept_the_same_representative_values():
	schema = json.loads((ROOT / "schemas" / "operation-graph.v0.json").read_text())
	metadata_schema = schema["properties"]["metadata"]
	assert set(metadata_schema["properties"]) == METADATA_FIELDS

	def schema_accepts(metadata: dict) -> bool:
		if any(key not in metadata_schema["properties"] for key in metadata):
			return False
		if any(key not in metadata for key in metadata_schema["required"]):
			return False
		for key, value in metadata.items():
			rule = metadata_schema["properties"][key]
			if "const" in rule and value != rule["const"]:
				return False
			if rule.get("type") == "string" and not isinstance(value, str):
				return False
			if rule.get("type") == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
				return False
			if "minimum" in rule and value < rule["minimum"]:
				return False
			if "pattern" in rule and (not isinstance(value, str) or re.search(rule["pattern"], value) is None):
				return False
		return True

	valid = example_graph()
	assert schema_accepts(valid["metadata"])
	assert validate_operation_graph(valid) == valid
	for replacement in (
		{"unknown": "field"},
		{"description": 7},
		{"graph_id": "bad/id"},
		{"name": "   "},
		{"version": True},
	):
		invalid = copy.deepcopy(valid)
		invalid["metadata"].update(replacement)
		assert not schema_accepts(invalid["metadata"])
		with pytest.raises(GraphValidationError):
			validate_operation_graph(invalid)


def test_parse_failures_are_clear():
	with pytest.raises(GraphParseError, match="Invalid JSON"):
		parse_operation_graph("{")
	with pytest.raises(GraphParseError, match="Unsupported"):
		parse_operation_graph("{}", syntax="toml")


def test_validation_reports_human_readable_paths_and_source_write_rejection():
	graph = example_graph()
	del graph["metadata"]["project_id"]
	graph["views"][0]["entity_id"] = "missing"
	graph["agents"][0]["capabilities"].append("source.write")
	with pytest.raises(GraphValidationError) as raised:
		validate_operation_graph(graph)
	message = str(raised.value)
	assert "$.metadata.project_id: is required" in message
	assert "$.views[0].entity_id: references unknown id 'missing'" in message
	assert "$.agents[0].capabilities: runtime agents may not receive source-code write" in message


def test_legacy_manifest_migration_is_explicit_deterministic_and_valid():
	manifest = json.loads(LEGACY.read_text())
	one = migrate_manifest_v0(manifest, tenant_id="tenant_acme", project_id="project_support")
	two = migrate_manifest_v0(manifest, tenant_id="tenant_acme", project_id="project_support")
	assert canonical_json_bytes(one) == canonical_json_bytes(two)
	assert one["metadata"]["migrated_from"] == "manifest.v0"
	assert one["connectors"][0]["configuration"]["uri"] == manifest["sources"][0]["uri"]
	assert one["views"][0]["entity_id"] == one["entities"][0]["id"]
	assert validate_operation_graph(one) == one


def test_legacy_migration_deduplicates_ids_and_normalizes_empty_fields():
	manifest = json.loads(LEGACY.read_text())
	manifest["sources"].append(copy.deepcopy(manifest["sources"][0]))
	duplicate_artifact = copy.deepcopy(manifest["artifacts"][0])
	duplicate_artifact["schema"] = [{"name": "", "type": ""}, {"name": None, "type": None}]
	manifest["artifacts"].append(duplicate_artifact)
	graph = migrate_manifest_v0(manifest, tenant_id="tenant_acme", project_id="project_support")
	assert len({item["id"] for item in graph["connectors"]}) == 2
	assert len({item["id"] for item in graph["entities"]}) == 2
	assert len({item["id"] for item in graph["views"]}) == 2
	assert graph["entities"][1]["fields"] == [
		{"name": "field_1", "type": "unknown"},
		{"name": "field_2", "type": "unknown"},
	]
	assert validate_operation_graph(graph) == graph


def test_content_addressed_revisions_are_deterministic_and_stale_writes_conflict(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support", clock=lambda: "2026-08-23T00:00:00Z")
	graph = example_graph()
	revision = store.create_revision(graph, expected_revision_hash=None)
	assert revision.revision_hash == hashlib.sha256(canonical_json_bytes(graph)).hexdigest()
	assert store.load_revision(revision.revision_hash) == revision
	assert store.list_revisions() == [revision]
	assert store.current_revision() == revision
	persisted = tmp_path / ".simulacra" / "operation-graph" / "revisions" / f"{revision.revision_hash}.json"
	assert persisted.read_text() == deterministic_json(json.loads(persisted.read_text()), indent=2)

	changed = copy.deepcopy(graph)
	changed["metadata"]["version"] = 1
	with pytest.raises(RevisionConflictError, match="stale"):
		store.create_revision(changed, expected_revision_hash=None)


def test_revision_hash_verification_detects_tampering(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_revision(example_graph(), expected_revision_hash=None)
	path = tmp_path / ".simulacra" / "operation-graph" / "revisions" / f"{revision.revision_hash}.json"
	record = json.loads(path.read_text())
	record["graph"]["metadata"]["name"] = "Tampered"
	path.write_text(deterministic_json(record, indent=2))
	with pytest.raises(ValueError, match="content hash verification"):
		store.load_revision(revision.revision_hash)


def test_historical_content_requires_audited_rollback_instead_of_create(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	first_graph = example_graph()
	first = store.create_revision(first_graph, expected_revision_hash=None)
	second_graph = copy.deepcopy(first_graph)
	second_graph["metadata"]["version"] = 1
	second = store.create_revision(second_graph, expected_revision_hash=first.revision_hash)
	with pytest.raises(RevisionConflictError, match="rollback_to"):
		store.create_revision(first_graph, expected_revision_hash=second.revision_hash)
	assert store.current_revision() == second
	assert store.list_rollbacks() == []
	assert store.create_revision(second_graph, expected_revision_hash=second.revision_hash) == second
	assert store.list_revisions() == [first, second]


def test_immutable_record_is_published_only_after_complete_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
	import simulacra.operation_graph.store as store_module

	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	destination = tmp_path / ".simulacra" / "operation-graph" / "revisions" / "publication-probe.json"
	entered_fsync = threading.Event()
	release_fsync = threading.Event()
	real_fsync = store_module.os.fsync

	def blocked_first_fsync(fd: int) -> None:
		if not entered_fsync.is_set():
			entered_fsync.set()
			assert release_fsync.wait(5)
		real_fsync(fd)

	monkeypatch.setattr(store_module.os, "fsync", blocked_first_fsync)
	errors: list[BaseException] = []

	def publish() -> None:
		try:
			store._write_immutable(destination, {"payload": "complete"})
		except BaseException as exc:
			errors.append(exc)

	writer = threading.Thread(target=publish)
	writer.start()
	assert entered_fsync.wait(5)
	assert not destination.exists(), "readers must not observe the destination while its temp file is incomplete"
	release_fsync.set()
	writer.join(5)
	assert not writer.is_alive()
	assert errors == []
	assert json.loads(destination.read_text()) == {"payload": "complete"}


def test_scope_and_storage_traversal_are_rejected(tmp_path: Path):
	with pytest.raises(ValueError, match="unsafe path"):
		OperationGraphStore(tmp_path, tenant_id="../escape", project_id="project_support")
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support")
	graph = example_graph()
	graph["metadata"]["project_id"] = "another_project"
	with pytest.raises(ValueError, match="scope"):
		store.create_revision(graph, expected_revision_hash=None)


def test_approval_binds_exact_hash_and_rollback_preserves_immutable_revisions(tmp_path: Path):
	store = OperationGraphStore(tmp_path, tenant_id="tenant_acme", project_id="project_support", clock=lambda: "2026-08-23T00:00:00Z")
	first = store.create_revision(example_graph(), expected_revision_hash=None)
	approval = store.approve_revision(first.revision_hash, actor_id="user_reviewer")
	assert approval.revision_hash == first.revision_hash
	assert approval.tenant_id == "tenant_acme"
	assert store.require_approved_revision(first.revision_hash) == first

	updated = example_graph()
	updated["metadata"]["version"] = 1
	updated["entities"][0]["fields"].append({"name": "priority", "type": "integer"})
	second = store.create_revision(updated, expected_revision_hash=first.revision_hash)
	with pytest.raises(UnapprovedRevisionError):
		store.require_approved_revision(second.revision_hash)
	rollback = store.rollback_to(
		first.revision_hash,
		expected_revision_hash=second.revision_hash,
		actor_id="user_operator",
		reason="Priority migration was not ready",
	)
	assert rollback.from_revision_hash == second.revision_hash
	assert rollback.target_revision_hash == first.revision_hash
	assert store.current_revision() == first
	assert store.list_revisions() == [first, second]
	assert store.list_rollbacks() == [rollback]


def test_structural_diff_reports_all_categories_and_impacts():
	old = example_graph()
	new = copy.deepcopy(old)
	new["entities"][0]["fields"].append({"name": "priority", "type": "integer"})
	new["views"] = []
	new["connectors"].append({"id": "connector_crm", "name": "CRM", "type": "http", "operations": ["write"]})
	diff = structural_diff(old, new)
	assert "$.connectors[id=connector_crm]" in diff.added
	assert "$.entities[id=entity_case].fields" in diff.changed
	assert "$.views[id=view_case_queue]" in diff.removed
	assert any("external-action exposure" in impact for impact in diff.security_impact)
	assert any("persisted-data" in impact for impact in diff.migration_impact)
	assert any("connector" in impact for impact in diff.test_impact)


def test_business_summary_is_readable():
	summary = business_summary(example_graph())
	assert summary.startswith("Support operations (version 0) defines 1 entities")
	assert "1 schedules are active" in summary
	assert "1 approval rules" in summary
