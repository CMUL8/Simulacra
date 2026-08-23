"""Durable audit, telemetry and runtime health surfaces."""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping

from .models import AuditEvent, TelemetryEvent, new_id, utc_now
from .policy import ApprovedGraph


class AuditService:
	def __init__(self, repository: Any, policy: ApprovedGraph, environment_id: str, *, clock: Callable[[], str] = utc_now): self.repository, self.policy, self.environment_id, self.clock = repository, policy, environment_id, clock
	def record(self, action: str, actor_id: str, result: str, *, payload: Mapping[str, Any] | None = None, event_id: str | None = None, correlation_id: str | None = None, trace_id: str | None = None) -> AuditEvent:
		if event_id is not None:
			for existing in self.list():
				if existing.id != event_id: continue
				if (existing.action, existing.actor_id, existing.result, existing.payload, existing.correlation_id, existing.trace_id) == (action, actor_id, result, dict(payload or {}), correlation_id, trace_id): return existing
				from .errors import RuntimeConflictError
				raise RuntimeConflictError("audit event id reused with different content")
		event = AuditEvent(event_id or new_id("evt"), self.policy.tenant_id, self.environment_id, self.policy.project_id, action, actor_id, result, copy.deepcopy(dict(payload or {})), correlation_id, trace_id, self.clock())
		return self.repository.append_audit(event)
	def list(self) -> list[AuditEvent]: return self.repository.list_audit(self.policy.tenant_id, self.environment_id, self.policy.project_id)


class TelemetryService:
	def __init__(self, repository: Any, policy: ApprovedGraph, environment_id: str, *, clock: Callable[[], str] = utc_now): self.repository, self.policy, self.environment_id, self.clock = repository, policy, environment_id, clock
	def emit(self, name: str, value: float = 1.0, *, attributes: Mapping[str, Any] | None = None) -> TelemetryEvent:
		return self.repository.append_telemetry(TelemetryEvent(new_id("metric"), self.policy.tenant_id, self.environment_id, self.policy.project_id, name, float(value), copy.deepcopy(dict(attributes or {})), self.clock()))
	def list(self) -> list[TelemetryEvent]: return self.repository.list_telemetry(self.policy.tenant_id, self.environment_id, self.policy.project_id)


class HealthService:
	def __init__(self, repository: Any, policy: ApprovedGraph, environment_id: str): self.repository, self.policy, self.environment_id = repository, policy, environment_id
	def liveness(self) -> dict[str, Any]: return {"status": "live", "service": "runtime"}
	def readiness(self) -> dict[str, Any]:
		try:
			self.repository.read_project(self.policy.tenant_id, self.environment_id, self.policy.project_id)
		except Exception as exc:
			return {"status": "not_ready", "service": "runtime", "reason": str(exc)}
		return {"status": "ready", "service": "runtime", "operation_graph_revision": self.policy.revision_hash}
	def health(self) -> dict[str, Any]:
		ready = self.readiness(); return {"status": "healthy" if ready["status"] == "ready" else "unhealthy", "liveness": self.liveness(), "readiness": ready}
	check = health
