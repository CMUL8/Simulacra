"""Durable event construction and non-mutating legacy SSE projection."""

from __future__ import annotations

from typing import Any

from .errors import ValidationError

from .models import ActorType, DomainEvent, iso_now, new_id, validate_scope_id


def make_domain_event(
	*,
	tenant_id: str,
	project_id: str,
	actor_type: ActorType | str,
	actor_id: str,
	action: str,
	result: str,
	payload: dict[str, Any] | None = None,
	event_id: str | None = None,
	task_id: str | None = None,
	operation_graph_version: str | None = None,
	application_version: str | None = None,
	environment_id: str | None = None,
	correlation_id: str | None = None,
	trace_id: str | None = None,
	timestamp: str | None = None,
) -> DomainEvent:
	validate_scope_id(tenant_id, "tenant_id")
	validate_scope_id(project_id, "project_id")
	validate_scope_id(actor_id, "actor_id")
	if not action.strip() or not result.strip():
		raise ValidationError("event action and result are required")
	return DomainEvent(
		id=event_id or new_id("evt"),
		actor_type=ActorType(actor_type),
		actor_id=actor_id,
		tenant_id=tenant_id,
		project_id=project_id,
		task_id=task_id,
		operation_graph_version=operation_graph_version,
		application_version=application_version,
		environment_id=environment_id,
		action=action.strip(),
		result=result.strip(),
		timestamp=timestamp or iso_now(),
		correlation_id=correlation_id,
		trace_id=trace_id,
		payload=dict(payload or {}),
	)


def project_legacy_event(event: DomainEvent) -> dict[str, Any]:
	"""Project a domain envelope without changing the source event."""

	payload = event.payload
	status = str(payload.get("status") or event.result).lower()
	if status in {"succeeded", "success", "ok", "done", "approved"}:
		status = "success"
	elif status in {"failed", "failure", "error", "rejected"}:
		status = "fail"
	elif status in {"cancelled", "canceled"}:
		status = "cancelled"
	else:
		status = "running"
	return {
		"id": event.id,
		"ts": event.timestamp,
		"type": str(payload.get("type") or event.action.split(".", 1)[0]),
		"label": str(payload.get("label") or event.action.replace("_", " ").replace(".", " · ").title()),
		"detail": str(payload.get("detail") or payload.get("body") or ""),
		"status": status,
		"meta": {
			"actor_type": event.actor_type.value,
			"actor_id": event.actor_id,
			"tenant_id": event.tenant_id,
			"project_id": event.project_id,
			"task_id": event.task_id,
			"correlation_id": event.correlation_id,
			"trace_id": event.trace_id,
		},
	}


class LegacyEventProjector:
	project = staticmethod(project_legacy_event)
