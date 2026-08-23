"""Operational runtime services independent of the builder/control plane."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping, Protocol

from .errors import (
	ActionExecutionError,
	ApprovalRequiredError,
	RuntimeAuthorizationError,
	RuntimeConflictError,
	RuntimeNotFoundError,
)
from .models import (
	ActionRecord,
	ApprovalDecision,
	ApprovalRequest,
	AuditEvent,
	EntityRecord,
	HumanTask,
	ScheduledJob,
	TelemetryEvent,
	WorkflowInstance,
	new_id,
	utc_now,
)
from .policy import ApprovedGraph
from .repository import JsonRuntimeRepository, RuntimeRepository
from .security import assert_opaque_credentials, thaw_json


def _parse_time(value: str) -> datetime:
	parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
	if parsed.tzinfo is None:
		raise ValueError("timestamp must be timezone-aware")
	return parsed.astimezone(UTC)


class EntityService:
	def __init__(self, repository: RuntimeRepository, policy: ApprovedGraph, environment_id: str, *, clock: Callable[[], str] = utc_now):
		self.repository, self.policy, self.environment_id, self.clock = repository, policy, environment_id, clock

	def create(self, entity_type: str, data: Mapping[str, Any], *, record_id: str | None = None) -> EntityRecord:
		validated = self.policy.validate_entity_data(entity_type, data)
		now = self.clock()
		record = EntityRecord(record_id or new_id("entity"), self.policy.tenant_id, self.environment_id, self.policy.project_id, entity_type, validated, self.policy.revision_hash, created_at=now, updated_at=now)
		return self.repository.create_entity(record)

	def get(self, record_id: str) -> EntityRecord:
		return self.repository.get_entity(self.policy.tenant_id, self.environment_id, self.policy.project_id, record_id)

	def query(self, entity_type: str | None = None, *, filters: dict[str, Any] | None = None) -> list[EntityRecord]:
		if entity_type is not None: self.policy.require_entity(entity_type)
		return self.repository.query_entities(self.policy.tenant_id, self.environment_id, self.policy.project_id, entity_type=entity_type, filters=filters)

	def update(self, record_id: str, data: Mapping[str, Any], *, expected_revision: int) -> EntityRecord:
		current = self.get(record_id)
		validated = self.policy.validate_entity_data(current.entity_type, data)
		updated = replace(current, data=validated, revision=current.revision + 1, updated_at=self.clock())
		return self.repository.save_entity(updated, expected_revision)

	def delete(self, record_id: str, *, expected_revision: int) -> None:
		self.repository.delete_entity(self.policy.tenant_id, self.environment_id, self.policy.project_id, record_id, expected_revision=expected_revision)


class WorkflowService:
	def __init__(self, repository: Any, policy: ApprovedGraph, environment_id: str, *, clock: Callable[[], str] = utc_now):
		self.repository, self.policy, self.environment_id, self.clock = repository, policy, environment_id, clock

	def start(self, workflow_id: str, *, entity_record_id: str | None = None, context: Mapping[str, Any] | None = None, instance_id: str | None = None) -> WorkflowInstance:
		workflow = self.policy.workflow(workflow_id)
		now = self.clock()
		instance = WorkflowInstance(instance_id or new_id("workflow"), self.policy.tenant_id, self.environment_id, self.policy.project_id, workflow_id, workflow["initial_state"], self.policy.revision_hash, entity_record_id, copy.deepcopy(dict(context or {})), created_at=now, updated_at=now)
		return self.repository.create_workflow(instance)

	def get(self, instance_id: str) -> WorkflowInstance:
		return self.repository.get_workflow(self.policy.tenant_id, self.environment_id, self.policy.project_id, instance_id)

	def transition(self, instance_id: str, target_state: str, *, expected_state: str, expected_revision: int, idempotency_key: str | None = None) -> WorkflowInstance:
		if idempotency_key:
			def change(state: dict[str, Any]) -> WorkflowInstance:
				command = state.setdefault("workflow_commands", {}).get(idempotency_key)
				fingerprint = {"instance_id": instance_id, "target_state": target_state, "expected_state": expected_state, "expected_revision": expected_revision}
				if command is not None:
					if command["fingerprint"] != fingerprint: raise RuntimeConflictError("workflow idempotency key reused for another command")
					return WorkflowInstance.from_dict(state["workflows"][instance_id])
				row = state["workflows"].get(instance_id)
				if row is None: raise RuntimeNotFoundError("workflow instance not found")
				current = WorkflowInstance.from_dict(row)
				if current.state != expected_state or current.revision != expected_revision: raise RuntimeConflictError("stale workflow state or revision")
				self.policy.assert_transition(current.workflow_id, current.state, target_state)
				updated = current if target_state == current.state else replace(current, state=target_state, revision=current.revision + 1, updated_at=self.clock())
				state["workflows"][instance_id] = updated.to_dict()
				state["workflow_commands"][idempotency_key] = {"fingerprint": fingerprint, "result_revision": updated.revision}
				return updated
			return self.repository.mutate_project(self.policy.tenant_id, self.environment_id, self.policy.project_id, change)
		current = self.get(instance_id)
		if current.state != expected_state:
			raise RuntimeConflictError(f"stale workflow state: expected {expected_state}, current {current.state}")
		if current.revision != expected_revision:
			raise RuntimeConflictError(f"stale workflow revision: expected {expected_revision}, current {current.revision}")
		self.policy.assert_transition(current.workflow_id, current.state, target_state)
		if target_state == current.state:
			return current
		updated = replace(current, state=target_state, revision=current.revision + 1, updated_at=self.clock())
		return self.repository.save_workflow(updated, expected_revision)

	command = transition


class HumanTaskService:
	def __init__(self, repository: Any, policy: ApprovedGraph, environment_id: str, *, clock: Callable[[], str] = utc_now):
		self.repository, self.policy, self.environment_id, self.clock = repository, policy, environment_id, clock

	def create(self, title: str, *, assignee_id: str | None = None, payload: Mapping[str, Any] | None = None) -> HumanTask:
		now = self.clock()
		return self.repository.create_human_task(HumanTask(new_id("htask"), self.policy.tenant_id, self.environment_id, self.policy.project_id, title, assignee_id=assignee_id, payload=copy.deepcopy(dict(payload or {})), created_at=now, updated_at=now))

	def queue(self, *, assignee_id: str | None = None, status: str = "open") -> list[HumanTask]:
		rows = self.repository.list_human_tasks(self.policy.tenant_id, self.environment_id, self.policy.project_id)
		return [row for row in rows if row.status == status and (assignee_id is None or row.assignee_id == assignee_id)]

	def complete(self, task_id: str, *, expected_revision: int) -> HumanTask:
		current = self.repository.get_human_task(self.policy.tenant_id, self.environment_id, self.policy.project_id, task_id)
		updated = replace(current, status="completed", revision=current.revision + 1, updated_at=self.clock())
		return self.repository.save_human_task(updated, expected_revision)


class ApprovalService:
	def __init__(self, repository: Any, policy: ApprovedGraph, environment_id: str, *, clock: Callable[[], str] = utc_now):
		self.repository, self.policy, self.environment_id, self.clock = repository, policy, environment_id, clock

	def new_request(self, action: str, requester_id: str, *, payload: Mapping[str, Any] | None = None, expires_at: str | None = None, approvals_required: int | None = None, allow_self_approval: bool | None = None) -> ApprovalRequest:
		required, count, self_allowed = self.policy.approval_policy(action)
		count = approvals_required if approvals_required is not None else count
		self_allowed = allow_self_approval if allow_self_approval is not None else self_allowed
		now = self.clock()
		return ApprovalRequest(new_id("approval"), self.policy.tenant_id, self.environment_id, self.policy.project_id, action, requester_id, approvals_required=max(1, count), allow_self_approval=self_allowed, payload=copy.deepcopy(dict(payload or {})), expires_at=expires_at, created_at=now, updated_at=now)

	def request(self, action: str, requester_id: str, *, payload: Mapping[str, Any] | None = None, expires_at: str | None = None, approvals_required: int | None = None, allow_self_approval: bool | None = None) -> ApprovalRequest:
		return self.repository.create_approval(self.new_request(action, requester_id, payload=payload, expires_at=expires_at, approvals_required=approvals_required, allow_self_approval=allow_self_approval))

	def get(self, approval_id: str) -> ApprovalRequest:
		return self.repository.get_approval(self.policy.tenant_id, self.environment_id, self.policy.project_id, approval_id)

	def decide(self, approval_id: str, *, actor_id: str, decision: str, actor_roles: tuple[str, ...] | list[str] | set[str] = (), reason: str = "", expected_revision: int | None = None) -> ApprovalRequest:
		if decision not in {"approved", "rejected"}: raise ValueError("decision must be approved or rejected")
		current = self.get(approval_id)
		if current.status != "pending": raise RuntimeConflictError("approval request is no longer pending")
		if expected_revision is not None and expected_revision != current.revision: raise RuntimeConflictError("stale approval revision")
		if actor_id == current.requester_id and not current.allow_self_approval: raise RuntimeAuthorizationError("requester may not self-approve")
		eligible = self.policy.approver_roles(current.action)
		if not eligible.intersection(actor_roles): raise RuntimeAuthorizationError(f"approval requires one of roles: {', '.join(sorted(eligible))}")
		if any(item["actor_id"] == actor_id for item in current.decisions): raise RuntimeConflictError("actor already decided this approval")
		now = self.clock()
		if current.expires_at and _parse_time(now) >= _parse_time(current.expires_at):
			return self.expire(approval_id, expected_revision=current.revision)
		decisions = [*current.decisions, ApprovalDecision(actor_id, decision, now, reason).to_dict()]
		approved = len([item for item in decisions if item["decision"] == "approved"])
		status = "rejected" if decision == "rejected" else ("approved" if approved >= current.approvals_required else "pending")
		updated = replace(current, decisions=decisions, status=status, revision=current.revision + 1, updated_at=now)
		return self.repository.save_approval(updated, current.revision)

	def expire(self, approval_id: str, *, expected_revision: int | None = None) -> ApprovalRequest:
		current = self.get(approval_id)
		if current.status != "pending": return current
		if expected_revision is not None and expected_revision != current.revision: raise RuntimeConflictError("stale approval revision")
		updated = replace(current, status="expired", revision=current.revision + 1, updated_at=self.clock())
		return self.repository.save_approval(updated, current.revision)


class ConnectorExecutor(Protocol):
	def __call__(self, connector: Mapping[str, Any], operation: str, payload: Mapping[str, Any], idempotency_key: str) -> Any: ...


class ConnectorGateway:
	"""Injected connector executors; no network clients or credentials live here."""
	def __init__(self, executors: Mapping[str, ConnectorExecutor] | None = None): self.executors = dict(executors or {})
	def _execute(self, connector: Mapping[str, Any], operation: str, payload: Mapping[str, Any], idempotency_key: str) -> Any:
		executor = self.executors.get(connector["id"]) or self.executors.get(connector["type"])
		if executor is None: raise ActionExecutionError(f"no executor registered for connector {connector['id']}")
		return executor(thaw_json(connector), operation, copy.deepcopy(dict(payload)), idempotency_key)


class ActionGateway:
	"""The sole external-write boundary, with approval and durable idempotency."""
	_READ_OPERATIONS = {"read", "get", "list", "search", "query", "fetch"}
	def __init__(self, repository: Any, policy: ApprovedGraph, environment_id: str, connector_gateway: ConnectorGateway, approval_service: ApprovalService, *, clock: Callable[[], str] = utc_now, base_backoff_seconds: float = 1.0, lease_seconds: int = 30):
		self.repository, self.policy, self.environment_id = repository, policy, environment_id
		self.connectors, self.approvals, self.clock = connector_gateway, approval_service, clock
		self.base_backoff_seconds = base_backoff_seconds
		self.lease_seconds = lease_seconds

	def submit(self, connector_id: str, operation: str, payload: Mapping[str, Any], *, requester_id: str, idempotency_key: str, consequential: bool | None = None, max_attempts: int = 3) -> ActionRecord:
		if not idempotency_key: raise ValueError("idempotency_key is required")
		if max_attempts < 1: raise ValueError("max_attempts must be positive")
		assert_opaque_credentials(payload, context="action payload")
		connector = self.policy.require_connector_operation(connector_id, operation)
		action_name = f"{connector_id}.{operation}"
		rule_required, _, _ = self.policy.approval_policy(action_name)
		# A caller may escalate a read to consequential, but can never downgrade a
		# write and thereby bypass the default approval boundary.
		is_consequential = operation.lower() not in self._READ_OPERATIONS or consequential is True
		needs_approval = rule_required or is_consequential
		now = self.clock()
		record = ActionRecord(new_id("action"), self.policy.tenant_id, self.environment_id, self.policy.project_id, connector_id, operation, idempotency_key, requester_id, copy.deepcopy(dict(payload)), "pending_approval" if needs_approval else "queued", is_consequential, max_attempts=max_attempts, created_at=now, updated_at=now)
		if needs_approval:
			approval = self.approvals.new_request(action_name, requester_id, payload={"action_id": record.id})
			record = replace(record, approval_id=approval.id)
			created, _ = self.repository.create_action_with_approval(record, approval)
			return created
		created = self.repository.create_action(record)
		if created.id != record.id: return created
		return self._execute(record)

	def _execute(self, record: ActionRecord) -> ActionRecord:
		def claim(state: dict[str, Any]) -> tuple[ActionRecord, bool]:
			current = ActionRecord.from_dict(state["actions"][record.id])
			if current.status in {"succeeded", "running"}: return current, False
			if current.status != "queued": raise RuntimeConflictError(f"action is not executable from {current.status}")
			now = self.clock()
			lease_until = (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(seconds=self.lease_seconds)).astimezone(UTC).isoformat().replace("+00:00", "Z")
			claimed = replace(current, status="running", attempts=current.attempts + 1, lease_owner=new_id("worker"), lease_until=lease_until, revision=current.revision + 1, updated_at=now)
			state["actions"][record.id] = claimed.to_dict()
			return claimed, True
		record, acquired = self.repository.mutate_project(self.policy.tenant_id, self.environment_id, self.policy.project_id, claim)
		if not acquired: return record
		connector = self.policy.require_connector_operation(record.connector_id, record.operation)
		try:
			result = self.connectors._execute(connector, record.operation, record.input, record.idempotency_key)
		except Exception as exc:
			attempts = record.attempts
			now = self.clock()
			dead = attempts >= record.max_attempts
			next_attempt = None if dead else (datetime.fromisoformat(now.replace("Z", "+00:00")) + timedelta(seconds=self.base_backoff_seconds * (2 ** (attempts - 1)))).astimezone(UTC).isoformat().replace("+00:00", "Z")
			failed = replace(record, status="dead_letter" if dead else "retry_wait", error=str(exc), next_attempt_at=next_attempt, lease_owner=None, lease_until=None, revision=record.revision + 1, updated_at=now)
			self.repository.save_action(failed, record.revision)
			raise ActionExecutionError(str(exc)) from exc
		completed = replace(record, status="succeeded", result=copy.deepcopy(result), error=None, next_attempt_at=None, lease_owner=None, lease_until=None, revision=record.revision + 1, updated_at=self.clock())
		return self.repository.save_action(completed, record.revision)

	def execute_approved(self, action_id: str) -> ActionRecord:
		record = self.repository.get_action(self.policy.tenant_id, self.environment_id, self.policy.project_id, action_id)
		if record.status == "succeeded": return record
		if record.status == "retry_wait": return self.retry(action_id)
		if record.status in {"running", "dead_letter"}: return record
		if record.approval_id is None: raise ApprovalRequiredError("consequential action has no approval")
		approval = self.approvals.get(record.approval_id)
		if approval.status != "approved": raise ApprovalRequiredError(f"action approval is {approval.status}")
		queued = replace(record, status="queued", revision=record.revision + 1, updated_at=self.clock())
		queued = self.repository.save_action(queued, record.revision)
		return self._execute(queued)

	def retry(self, action_id: str) -> ActionRecord:
		record = self.repository.get_action(self.policy.tenant_id, self.environment_id, self.policy.project_id, action_id)
		if record.status == "dead_letter": return record
		if record.status != "retry_wait": raise RuntimeConflictError(f"action is not awaiting retry: {record.status}")
		now = self.clock()
		if record.next_attempt_at and _parse_time(now) < _parse_time(record.next_attempt_at): raise RuntimeConflictError("action retry backoff has not elapsed")
		queued = replace(record, status="queued", revision=record.revision + 1, updated_at=now)
		return self._execute(self.repository.save_action(queued, record.revision))

	def recover_stale(self, action_id: str) -> ActionRecord:
		"""Recover an expired claim without directly invoking the connector."""
		now_text = self.clock()
		now = _parse_time(now_text)
		def change(state: dict[str, Any]) -> ActionRecord:
			row = state["actions"].get(action_id)
			if row is None: raise RuntimeNotFoundError("action record not found")
			current = ActionRecord.from_dict(row)
			if current.status != "running": raise RuntimeConflictError("action is not running")
			if current.lease_until is None or _parse_time(current.lease_until) > now:
				raise RuntimeConflictError("action lease has not expired")
			dead = current.attempts >= current.max_attempts
			recovered = replace(
				current,
				status="dead_letter" if dead else "retry_wait",
				error="execution lease expired before durable completion",
				next_attempt_at=None if dead else now_text,
				lease_owner=None,
				lease_until=None,
				revision=current.revision + 1,
				updated_at=now_text,
			)
			state["actions"][action_id] = recovered.to_dict()
			return recovered
		return self.repository.mutate_project(self.policy.tenant_id, self.environment_id, self.policy.project_id, change)

	def dead_letters(self) -> list[ActionRecord]:
		return [record for record in self.repository.list_actions(self.policy.tenant_id, self.environment_id, self.policy.project_id) if record.status == "dead_letter"]

	execute = submit
