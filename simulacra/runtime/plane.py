"""Composition root for an independently deployable runtime plane."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from simulacra.operation_graph import OperationGraphStore

from .agents import RuntimeAgentSupervisor, RuntimeTool
from .observability import AuditService, HealthService, TelemetryService
from .policy import ApprovedGraph
from .repository import JsonRuntimeRepository
from .scheduler import Scheduler
from .services import (
	ActionGateway,
	ApprovalService,
	ConnectorExecutor,
	ConnectorGateway,
	EntityService,
	HumanTaskService,
	WorkflowService,
)


class RuntimePlane:
	"""Runtime facade whose only governance input is a frozen approved graph.

	After construction this object has no reference to the Operation Graph store or
	any control-plane service. It therefore remains operational during control-plane
	outages, while every service stays bound to the approved revision hash.
	"""

	def __init__(self, repository: JsonRuntimeRepository, approved_graph: ApprovedGraph, environment_id: str, *, connector_executors: Mapping[str, ConnectorExecutor] | None = None, agent_tools: Mapping[str, RuntimeTool] | None = None):
		if not isinstance(approved_graph, ApprovedGraph):
			raise TypeError("runtime plane requires an ApprovedGraph")
		approved_graph.assert_verified()
		self.repository = repository
		self.policy = approved_graph
		self.environment_id = environment_id
		self.entities = EntityService(repository, approved_graph, environment_id)
		self.workflows = WorkflowService(repository, approved_graph, environment_id)
		self.human_tasks = HumanTaskService(repository, approved_graph, environment_id)
		self.approvals = ApprovalService(repository, approved_graph, environment_id)
		self.connectors = ConnectorGateway(connector_executors)
		self.actions = ActionGateway(repository, approved_graph, environment_id, self.connectors, self.approvals)
		self.scheduler = Scheduler(repository, approved_graph, environment_id)
		self.agents = RuntimeAgentSupervisor(approved_graph, agent_tools, action_gateway=self.actions)
		self.audit = AuditService(repository, approved_graph, environment_id)
		self.telemetry = TelemetryService(repository, approved_graph, environment_id)
		self.health = HealthService(repository, approved_graph, environment_id)

	@classmethod
	def from_approved_revision(cls, repository_root: str | Path, graph_store: OperationGraphStore, revision_hash: str, *, environment_id: str, connector_executors: Mapping[str, ConnectorExecutor] | None = None, agent_tools: Mapping[str, RuntimeTool] | None = None) -> "RuntimePlane":
		approved = ApprovedGraph.from_store(graph_store, revision_hash)
		return cls(JsonRuntimeRepository(repository_root), approved, environment_id, connector_executors=connector_executors, agent_tools=agent_tools)

	bootstrap = from_approved_revision
