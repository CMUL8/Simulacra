from __future__ import annotations

import re

from typing import Any, Mapping

from .errors import GraphValidationError, ValidationIssue
from .validation import SCHEMA_ID, validate_operation_graph


def _id(prefix: str, value: str, index: int) -> str:
	clean = re.sub(r"[^A-Za-z0-9._:-]+", "-", value).strip("-._:").lower()
	return f"{prefix}_{clean or 'legacy'}_{index + 1}"


def _legacy_text(value: Any, fallback: str) -> str:
	text = "" if value is None else str(value).strip()
	return text or fallback


def migrate_manifest_v0(
	manifest: Mapping[str, Any],
	*,
	tenant_id: str,
	project_id: str,
) -> dict[str, Any]:
	"""Deterministically adapt the readable legacy manifest.v0 shape into a V0 graph."""
	issues: list[ValidationIssue] = []
	for key in ("simulacra_version", "run_id", "created_at", "task", "sources", "artifacts", "gates", "prime"):
		if key not in manifest:
			issues.append(ValidationIssue(f"$.{key}", "is required by legacy manifest.v0"))
	if not tenant_id or not project_id:
		issues.append(ValidationIssue("$.metadata", "tenant_id and project_id must be explicit"))
	if issues:
		raise GraphValidationError(issues)

	sources = manifest.get("sources") if isinstance(manifest.get("sources"), list) else []
	artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), list) else []
	connectors = []
	for index, source in enumerate(sources):
		if not isinstance(source, Mapping):
			continue
		uri = _legacy_text(source.get("uri"), "")
		connectors.append(
			{
				"id": _id("connector", uri, index),
				"name": uri or f"Legacy source {index + 1}",
				"type": _legacy_text(source.get("type"), "uri"),
				"configuration": {
					"uri": uri,
					**({"content_hash": source["content_hash"]} if source.get("content_hash") else {}),
				},
				"operations": ["read"],
			}
		)

	entities = []
	views = []
	for index, artifact in enumerate(artifacts):
		if not isinstance(artifact, Mapping):
			continue
		path = _legacy_text(artifact.get("path"), "")
		entity_id = _id("entity", path, index)
		fields = [
			{
				"name": _legacy_text(field.get("name"), f"field_{field_index + 1}"),
				"type": _legacy_text(field.get("type"), "unknown"),
			}
			for field_index, field in enumerate(artifact.get("schema", []))
			if isinstance(field, Mapping)
		]
		entities.append(
			{
				"id": entity_id,
				"name": path or f"Legacy artifact {index + 1}",
				"kind": _legacy_text(artifact.get("kind"), "other"),
				"fields": fields,
				**({"row_count": artifact["row_count"]} if "row_count" in artifact else {}),
			}
		)
		views.append({"id": _id("view", path, index), "name": path or f"Legacy view {index + 1}", "entity_id": entity_id})

	run_id = _legacy_text(manifest["run_id"], "legacy-run")
	graph: dict[str, Any] = {
		"metadata": {
			"schema_id": SCHEMA_ID,
			"graph_id": _id("ogr", run_id, 0),
			"tenant_id": tenant_id,
			"project_id": project_id,
			"name": str(manifest["task"]),
			"version": 0,
			"description": f"Migrated legacy run {run_id}",
			"migrated_from": "manifest.v0",
		},
		"entities": entities,
		"views": views,
		"workflows": [
			{
				"id": "workflow_legacy_run",
				"name": "Legacy run",
				"states": ["pending", "running", "passed", "failed"],
				"initial_state": "pending",
			}
		],
		"agents": [
			{
				"id": "agent_legacy_builder",
				"name": "Legacy Prime builder",
				"actor_type": "builder_agent",
				"provider": "prime",
				"capabilities": ["artifact.write"],
			}
		],
		"automations": [
			{
				"id": "automation_legacy_run",
				"name": "Legacy build run",
				"workflow_id": "workflow_legacy_run",
				"trigger": "manual",
			}
		],
		"connectors": connectors,
		"permissions": [
			{
				"id": "permission_legacy_builder",
				"name": "Legacy builder access",
				"principals": ["agent_legacy_builder"],
				"actions": ["graph.read", "artifact.write"],
				"resources": ["project"],
			}
		],
		"approval_rules": [
			{
				"id": "approval_legacy_gate",
				"name": "Legacy gate",
				"required": manifest.get("gates", {}).get("status") != "pass",
				"approvals_required": 1,
				"actions": ["build.accept"],
			}
		],
		"schedules": [],
	}
	return validate_operation_graph(graph)
