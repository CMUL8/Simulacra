"""Architect-agent integration for project Operation Graph proposals."""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from simulacra.harnesses import (
	AgentRunRequest,
	HarnessConfig,
	NetworkPolicy,
	TaskType,
	TerminalStatus,
	create_harness,
)
from simulacra.operation_graph import OperationGraphStore, canonical_json_bytes, deterministic_json, validate_operation_graph
from simulacra.operation_graph.publication import atomic_write_project_file
from simulacra.operation_graph.validation import AREAS, SCHEMA_ID

from .mutation_authorization import require_room_mutation_authority, room_mutation_commit
from .runs import ProjectState, project_dir, save_state


_OUTPUT_SCHEMA = {
	"type": "object",
	"required": ["metadata", *AREAS],
	"properties": {
		"metadata": {"type": "object"},
		**{area: {"type": "array"} for area in AREAS},
	},
	"additionalProperties": False,
}


def _identifier(prefix: str, value: str) -> str:
	clean = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:48]
	return f"{prefix}_{clean or 'record'}"


def _safe_scaffold(state: ProjectState, *, architect_error: str | None) -> dict[str, Any]:
	"""Neutral executable draft used when the selected architect is unavailable.

	This is a platform scaffold, not an industry template. The user must still
	review and approve its exact immutable revision before any build consumes it.
	"""
	name = state.app_config.title or state.goal or "Operations workspace"
	entity_id = _identifier("entity", name)
	workflow_id = _identifier("workflow", name)
	agent_id = _identifier("agent", name)
	description = state.goal or state.prompt
	if architect_error:
		description = f"{description} Architect unavailable; review this neutral scaffold before approval."
	return validate_operation_graph({
		"metadata": {
			"schema_id": SCHEMA_ID,
			"graph_id": _identifier("ogr", state.id),
			"tenant_id": state.tenant_id,
			"project_id": state.id,
			"name": name,
			"version": 0,
			"description": description[:1000],
		},
		"entities": [{
			"id": entity_id,
			"name": "Record",
			"fields": [
				{"name": "title", "type": "string"},
				{"name": "description", "type": "string"},
				{"name": "status", "type": "string"},
			],
		}],
		"views": [{"id": "view_records", "name": "Records", "entity_id": entity_id}],
		"workflows": [{
			"id": workflow_id,
			"name": "Review record",
			"states": ["draft", "submitted", "in_review", "approved", "rejected"],
			"initial_state": "draft",
		}],
		"agents": [{
			"id": agent_id,
			"name": "Review assistant",
			"actor_type": "runtime_agent",
			"capabilities": ["entity.read", "task.propose"],
		}],
		"automations": [{
			"id": "automation_start_review",
			"name": "Start review on submission",
			"workflow_id": workflow_id,
			"trigger": "entity.submitted",
		}],
		"connectors": [],
		"permissions": [{
			"id": "permission_project_members",
			"name": "Project member access",
			"principals": ["owner", "admin", "member"],
			"actions": ["entity.read", "entity.write", "workflow.transition"],
			"resources": [entity_id, workflow_id],
		}],
		"approval_rules": [{
			"id": "approval_final_decision",
			"name": "Final decision approval",
			"required": True,
			"approvals_required": 1,
			"approver_roles": ["owner", "admin"],
			"actions": [f"{workflow_id}.approve"],
		}],
		"schedules": [],
	})


def bootstrap_graph_candidate_hash(state: ProjectState) -> str:
    """Hash the deterministic bootstrap graph before any revision is written."""
    return hashlib.sha256(canonical_json_bytes(_safe_scaffold(state, architect_error=None))).hexdigest()


def build_bootstrap_graph(
    state: ProjectState,
    *,
    actor_id: str,
    expected_tenant_id: str | None = None,
    expected_project_id: str | None = None,
    expected_graph_hash: str | None = None,
) -> tuple[str, str]:
	"""Create durable bootstrap graph bytes without using an ephemeral job.

		The caller publishes the exact revision head only after recording durable
		intent. This deliberately avoids ``init_plan``/``start_job``.
	"""
	if not actor_id.strip():
		raise ValueError("bootstrap graph requires an owner")
	if expected_tenant_id is not None and expected_tenant_id != state.tenant_id:
		raise ValueError("bootstrap graph tenant does not match its reservation")
	if expected_project_id is not None and expected_project_id != state.id:
		raise ValueError("bootstrap graph project does not match its reservation")
	graph = _safe_scaffold(state, architect_error=None)
	candidate_hash = hashlib.sha256(canonical_json_bytes(graph)).hexdigest()
	if expected_graph_hash is not None and expected_graph_hash != candidate_hash:
		raise ValueError("bootstrap graph input no longer matches its reservation")
	store = OperationGraphStore(project_dir(state.id), tenant_id=state.tenant_id, project_id=state.id)
	revision = store.create_immutable_revision(graph)
	if revision.revision_hash != candidate_hash:
		raise ValueError("bootstrap graph revision does not match its reservation")
	final = store.finalize_exact_revision_head(
		tenant_id=state.tenant_id, project_id=state.id,
		revision_hash=revision.revision_hash, canonical_graph_hash=revision.revision_hash,
	)
	return final.revision_hash, "ready_for_approval"


def propose_operation_graph(state: ProjectState, *, actor_id: str) -> dict[str, Any]:
	"""Create an immutable proposal only for a current Room owner/admin."""
	require_room_mutation_authority(state.id, tenant_id=state.tenant_id, actor_id=actor_id)
	root = project_dir(state.id)
	store = OperationGraphStore(root, tenant_id=state.tenant_id, project_id=state.id)
	current = store.current_revision()
	if current is not None:
		return current.graph
	config = HarnessConfig.from_env()
	harness = create_harness(config)
	request = AgentRunRequest(
		project_id=state.id,
		environment_id="builder",
		workspace=root,
		prompt=(
			"Act as the CMUL8 Architect Agent. Convert the following business requirement into a generic, "
			"operational CMUL8 Operation Graph. Return only JSON matching the requested schema. Include "
			"the exact tenant_id and project_id supplied below. Runtime agents may never receive source-write "
			"capabilities. Consequential actions require owner/admin approval. Do not apply an industry template.\n\n"
			f"tenant_id: {state.tenant_id}\nproject_id: {state.id}\nrequirement: {state.prompt}"
		),
		role="architect",
		task_type=TaskType.ARCHITECT,
		read_paths=(root / "inputs" / "data-room",),
		write_paths=(),
		network_policy=NetworkPolicy.DENY,
		config=config,
		metadata={"output_schema": _OUTPUT_SCHEMA},
	)
	result = asyncio.run(harness.run(request))
	# The architect call can be long-running; a role change while it ran must
	# prevent the result from becoming a durable graph revision.
	require_room_mutation_authority(state.id, tenant_id=state.tenant_id, actor_id=actor_id)
	error: str | None = None
	graph: Mapping[str, Any] | None = result.structured_output
	try:
		if result.status is not TerminalStatus.SUCCEEDED or not graph:
			raise ValueError(str(dict(result.error or {})) or "architect returned no graph")
		validated = validate_operation_graph(graph)
		metadata = validated["metadata"]
		if metadata["tenant_id"] != state.tenant_id or metadata["project_id"] != state.id:
			raise ValueError("architect graph scope does not match the project")
		graph = validated
	except Exception as exc:
		error = str(exc)
		graph = _safe_scaffold(state, architect_error=error)
	# A room role update and immutable graph write are serialized under the same
	# durable room lock. A demotion that commits first therefore prevents any
	# revision/head write; one that commits later observes a completed authorized
	# proposal instead of racing halfway through it.
	with room_mutation_commit(state.id, tenant_id=state.tenant_id, actor_id=actor_id):
		revision = store.create_revision(graph, expected_revision_hash=None)
		state.prime = {
			**state.prime,
			"architect_harness": result.harness,
			"architect_model": result.model_id,
			"architect_status": "proposed" if error is None else "scaffold_review_required",
			"architect_error": error,
			"operation_graph_revision": revision.revision_hash,
		}
		save_state(state)
	# Defense in depth after publication; the atomic lock above is the authority
	# boundary that eliminates the check/write race.
	require_room_mutation_authority(state.id, tenant_id=state.tenant_id, actor_id=actor_id)
	return revision.graph


def approved_graph_path(state: ProjectState) -> Path | None:
	store = OperationGraphStore(project_dir(state.id), tenant_id=state.tenant_id, project_id=state.id)
	current = store.current_revision()
	if current is None:
		raise PermissionError("An Operation Graph proposal is required before building")
	approved = store.require_approved_revision(current.revision_hash)
	return atomic_write_project_file(
		project_dir(state.id),
		Path("work") / "approved-operation-graph.json",
		deterministic_json(approved.graph, indent=2).encode("utf-8"),
	)
