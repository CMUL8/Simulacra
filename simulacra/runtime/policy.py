"""Executable policy projected only from an approved Operation Graph revision."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

from simulacra.operation_graph import OperationGraphStore, validate_operation_graph

from .errors import InvalidTransitionError, RuntimeAuthorizationError, RuntimeNotFoundError, RuntimeScopeError
from .security import assert_opaque_credentials, freeze_json

_VERIFIED_APPROVAL = object()


@dataclass(frozen=True, init=False)
class ApprovedGraph:
	graph: Mapping[str, Any]
	revision_hash: str
	approval_proof: tuple[Mapping[str, Any], ...]
	_verified_approval: object

	def __init__(self, *args: Any, **kwargs: Any) -> None:
		raise RuntimeAuthorizationError("ApprovedGraph can only be constructed by from_store after exact approval verification")

	@classmethod
	def from_store(cls, store: OperationGraphStore, revision_hash: str) -> "ApprovedGraph":
		revision = store.require_approved_revision(revision_hash)
		approvals = store.list_approvals(revision_hash)
		validated = validate_operation_graph(revision.graph)
		for connector in validated.get("connectors", []):
			assert_opaque_credentials(connector.get("configuration", {}), context=f"connector {connector.get('id', '<unknown>')} configuration")
		instance = object.__new__(cls)
		object.__setattr__(instance, "graph", freeze_json(validated))
		object.__setattr__(instance, "revision_hash", revision.revision_hash)
		object.__setattr__(instance, "approval_proof", tuple(freeze_json({
			"approval_id": approval.approval_id,
			"actor_id": approval.actor_id,
			"decision": approval.decision,
			"revision": approval.revision,
			"revision_hash": approval.revision_hash,
			"created_at": approval.created_at,
		}) for approval in approvals))
		object.__setattr__(instance, "_verified_approval", _VERIFIED_APPROVAL)
		return instance

	def assert_verified(self) -> None:
		if getattr(self, "_verified_approval", None) is not _VERIFIED_APPROVAL or not self.approval_proof:
			raise RuntimeAuthorizationError("runtime policy lacks exact approval proof")

	@property
	def tenant_id(self) -> str:
		return self.graph["metadata"]["tenant_id"]

	@property
	def project_id(self) -> str:
		return self.graph["metadata"]["project_id"]

	def assert_scope(self, tenant_id: str, project_id: str) -> None:
		if (tenant_id, project_id) != (self.tenant_id, self.project_id):
			raise RuntimeScopeError("operation graph scope does not match runtime request")

	def item(self, area: str, item_id: str) -> Mapping[str, Any]:
		for item in self.graph.get(area, []):
			if item.get("id") == item_id:
				return item
		raise RuntimeNotFoundError(f"{area} item not present in approved Operation Graph: {item_id}")

	def require_entity(self, entity_type: str) -> Mapping[str, Any]:
		return self.item("entities", entity_type)

	def validate_entity_data(self, entity_type: str, data: Mapping[str, Any]) -> dict[str, Any]:
		entity = self.require_entity(entity_type)
		fields = {field["name"]: field.get("type", "unknown").lower() for field in entity.get("fields", [])}
		unknown = sorted(set(data) - set(fields))
		if unknown and not entity.get("allow_additional_fields", False):
			raise ValueError(f"entity {entity_type} contains undeclared fields: {', '.join(unknown)}")
		types: dict[str, tuple[type, ...]] = {
			"string": (str,), "integer": (int,), "number": (int, float), "boolean": (bool,),
			"object": (dict,), "array": (list,), "null": (type(None),),
		}
		for name, value in data.items():
			expected = fields.get(name, "unknown")
			allowed = types.get(expected)
			if allowed is not None and (not isinstance(value, allowed) or expected in {"integer", "number"} and isinstance(value, bool)):
				raise ValueError(f"entity field {name} must be {expected}")
		return copy.deepcopy(dict(data))

	def require_connector_operation(self, connector_id: str, operation: str) -> Mapping[str, Any]:
		connector = self.item("connectors", connector_id)
		if operation not in connector.get("operations", []):
			raise RuntimeAuthorizationError(f"connector operation is not graph-allowed: {connector_id}.{operation}")
		return connector

	def workflow(self, workflow_id: str) -> Mapping[str, Any]:
		return self.item("workflows", workflow_id)

	def assert_transition(self, workflow_id: str, source: str, target: str) -> None:
		workflow = self.workflow(workflow_id)
		states = workflow["states"]
		if source not in states or target not in states:
			raise InvalidTransitionError(f"state is not declared by workflow {workflow_id}: {source} -> {target}")
		if source == target:
			return
		explicit = workflow.get("transitions")
		if explicit is not None:
			edges: set[tuple[str, str]] = set()
			if isinstance(explicit, Mapping):
				for origin, destinations in explicit.items():
					for destination in destinations if isinstance(destinations, tuple) else (destinations,):
						edges.add((str(origin), str(destination)))
			elif isinstance(explicit, tuple):
				for edge in explicit:
					if isinstance(edge, Mapping):
						edges.add((str(edge.get("from")), str(edge.get("to"))))
					elif isinstance(edge, tuple) and len(edge) == 2:
						edges.add((str(edge[0]), str(edge[1])))
			if (source, target) not in edges:
				raise InvalidTransitionError(f"transition is not reachable: {source} -> {target}")
		elif states.index(target) != states.index(source) + 1:
			raise InvalidTransitionError(f"transition is not the next reachable state: {source} -> {target}")

	def approval_policy(self, action: str) -> tuple[bool, int, bool]:
		for rule in self.graph.get("approval_rules", []):
			if action in rule.get("actions", []):
				return bool(rule.get("required", True)), max(1, int(rule.get("approvals_required", 1))), bool(rule.get("allow_self_approval", False))
		return False, 1, False

	def runtime_agent(self, agent_id: str) -> Mapping[str, Any]:
		agent = self.item("agents", agent_id)
		if agent.get("actor_type") != "runtime_agent":
			raise RuntimeAuthorizationError(f"agent is not a runtime agent: {agent_id}")
		return agent

	def agent_allows(self, agent_id: str, capability: str, resource: str | None = None) -> bool:
		agent = self.runtime_agent(agent_id)
		if capability not in set(agent.get("capabilities", [])) | set(agent.get("tools", [])):
			return False
		permissions = [rule for rule in self.graph.get("permissions", []) if agent_id in rule.get("principals", [])]
		if not permissions:
			return True
		return any(capability in rule.get("actions", []) and (resource is None or resource in rule.get("resources", [])) for rule in permissions)
