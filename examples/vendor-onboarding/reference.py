"""Executable vendor-onboarding reference composed from public Simulacra contracts."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from simulacra.collaboration import (
	AuthorizationError as CollaborationAuthorizationError,
	CollaborationService,
	JsonCollaborationRepository,
	NotFoundError as CollaborationNotFoundError,
	ReviewDecision,
	TaskState,
)
from simulacra.operation_graph import OperationGraphStore, load_operation_graph
from simulacra.runtime import (
	ActionExecutionError,
	ReadOnlyDataTool,
	RuntimeAuthorizationError,
	RuntimePlane,
)

TENANT_ID = "tenant_reference"
PROJECT_ID = "project_vendor_onboarding"
ENVIRONMENT_ID = "env_reference"
GRAPH_PATH = Path(__file__).with_name("operation-graph.json")


class ReferenceClock:
	"""Small deterministic UTC clock used by the executable scenarios."""

	def __init__(self, start: datetime | None = None):
		self.current = start or datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

	def __call__(self) -> str:
		return self.current.isoformat().replace("+00:00", "Z")

	def advance(self, seconds: int) -> None:
		self.current += timedelta(seconds=seconds)


class NotificationRecorder:
	"""Injected connector that models durable provider-side idempotency."""

	def __init__(self, *, failures: int = 0):
		self.failure_budget = failures
		self.attempts: list[str] = []
		self.deliveries: dict[str, dict[str, Any]] = {}

	def __call__(
		self,
		connector: Mapping[str, Any],
		operation: str,
		payload: Mapping[str, Any],
		idempotency_key: str,
	) -> dict[str, Any]:
		self.attempts.append(idempotency_key)
		if idempotency_key in self.deliveries:
			return {"delivery": "deduplicated", "idempotency_key": idempotency_key}
		if self.failure_budget:
			self.failure_budget -= 1
			raise OSError("simulated notification provider outage")
		self.deliveries[idempotency_key] = copy.deepcopy(dict(payload))
		return {"delivery": "accepted", "idempotency_key": idempotency_key}


@dataclass(frozen=True)
class VendorCase:
	vendor_key: str
	vendor_record_id: str
	workflow_id: str
	collaboration_task_id: str
	human_task_id: str
	action_id: str
	approval_id: str
	submitted_by: str


class VendorOnboardingReference:
	"""End-to-end reference service; all durable state belongs to existing planes."""

	def __init__(self, state_root: str | Path, notifier: NotificationRecorder, *, clock: ReferenceClock | None = None):
		self.state_root = Path(state_root).resolve()
		self.state_root.mkdir(parents=True, exist_ok=True)
		self.notifier = notifier
		self.clock = clock or ReferenceClock()
		self.graph = load_operation_graph(GRAPH_PATH)

		project_root = self.state_root / "project"
		project_root.mkdir(exist_ok=True)
		graph_store = OperationGraphStore(project_root, tenant_id=TENANT_ID, project_id=PROJECT_ID, clock=self.clock)
		current = graph_store.current_revision()
		if current is None:
			current = graph_store.create_revision(self.graph, expected_revision_hash=None)
			graph_store.approve_revision(current.revision_hash, actor_id="governance_admin")
		elif current.graph != self.graph:
			raise ValueError("state directory belongs to a different Operation Graph")
		else:
			try:
				graph_store.require_approved_revision(current.revision_hash)
			except Exception:
				graph_store.approve_revision(current.revision_hash, actor_id="governance_admin")

		data_root = self.state_root / "data"
		self.runtime = RuntimePlane.from_approved_revision(
			data_root,
			graph_store,
			current.revision_hash,
			environment_id=ENVIRONMENT_ID,
			connector_executors={"connector_notifications": notifier},
			agent_tools={
				"risk.exception.evaluate": ReadOnlyDataTool(
					"risk.exception.evaluate",
					"risk.exception.evaluate",
					lambda payload: {
						"exception_required": int(payload["risk_score"]) >= 70,
						"disposition": "manual_exception" if int(payload["risk_score"]) >= 70 else "standard_review",
					},
				),
			},
		)
		for service in (
			self.runtime.entities, self.runtime.workflows, self.runtime.human_tasks,
			self.runtime.approvals, self.runtime.actions, self.runtime.scheduler,
			self.runtime.audit, self.runtime.telemetry,
		):
			service.clock = self.clock

		self.collaboration_repository = JsonCollaborationRepository(data_root)
		self.collaboration = CollaborationService(self.collaboration_repository)
		self._ensure_collaboration_room()

	def _ensure_collaboration_room(self) -> None:
		try:
			self.collaboration_repository.get_room(TENANT_ID, PROJECT_ID)
			return
		except CollaborationNotFoundError:
			pass
		room = self.collaboration.create_room(
			tenant_id=TENANT_ID,
			project_id=PROJECT_ID,
			creator_id="operations_admin",
		)
		for actor_id, role in (
			("vendor_reviewer", "vendor_reviewer"),
			("approval_manager", "approver"),
			("agent_exception", "runtime_agent"),
		):
			room = self.collaboration.add_member(
				tenant_id=TENANT_ID,
				project_id=PROJECT_ID,
				actor_id="operations_admin",
				member_id=actor_id,
				role=role,
				expected_revision=room.revision,
			)

	def _audit(self, vendor_key: str, stage: str, actor_id: str, result: str, payload: Mapping[str, Any]) -> None:
		self.runtime.audit.record(
			f"vendor.{stage}",
			actor_id,
			result,
			payload=payload,
			event_id=f"evt_{vendor_key}_{stage}_{result}",
			correlation_id=f"vendor_{vendor_key}",
		)
		self.runtime.telemetry.emit(
			"vendor_onboarding.stage",
			attributes={"stage": stage, "result": result, "vendor_key": vendor_key},
		)

	def submit_vendor(
		self,
		vendor_key: str,
		*,
		name: str,
		risk_score: int,
		documents: tuple[str, ...] = ("tax_form", "bank_letter"),
		submitted_by: str = "vendor_reviewer",
	) -> VendorCase:
		vendor_id = f"entity_vendor_{vendor_key}"
		vendor = self.runtime.entities.create(
			"entity_vendor",
			{
				"name": name,
				"status": "submitted",
				"risk_score": risk_score,
				"submitted_by": submitted_by,
				"exception_required": False,
			},
			record_id=vendor_id,
		)
		workflow = self.runtime.workflows.start(
			"workflow_vendor_onboarding",
			entity_record_id=vendor.id,
			instance_id=f"workflow_vendor_{vendor_key}",
		)
		workflow = self.runtime.workflows.transition(
			workflow.id, "documents_pending", expected_state="submitted", expected_revision=0,
			idempotency_key=f"vendor_{vendor_key}_documents",
		)
		for index, document_type in enumerate(documents, start=1):
			self.runtime.entities.create(
				"entity_document",
				{"vendor_record_id": vendor.id, "document_type": document_type, "status": "received"},
				record_id=f"entity_document_{vendor_key}_{index}",
			)
		workflow = self.runtime.workflows.transition(
			workflow.id, "risk_review", expected_state="documents_pending", expected_revision=1,
			idempotency_key=f"vendor_{vendor_key}_risk",
		)
		risk = self.runtime.agents.invoke(
			"agent_exception",
			"risk.exception.evaluate",
			{"risk_score": risk_score, "vendor_record_id": vendor.id},
			resource="entity_vendor",
		)
		vendor = self.runtime.entities.update(
			vendor.id,
			{
				**vendor.data,
				"status": "approval_pending",
				"exception_required": bool(risk["exception_required"]),
			},
			expected_revision=vendor.revision,
		)
		workflow = self.runtime.workflows.transition(
			workflow.id, "approval_pending", expected_state="risk_review", expected_revision=2,
			idempotency_key=f"vendor_{vendor_key}_approval",
		)
		human_task = self.runtime.human_tasks.create(
			f"Review vendor {name}",
			assignee_id="approval_manager",
			payload={"vendor_record_id": vendor.id, "exception_required": risk["exception_required"]},
		)
		collaboration_task = self.collaboration.create_task(
			tenant_id=TENANT_ID,
			project_id=PROJECT_ID,
			actor_id=submitted_by,
			title=f"Approve vendor {name}",
			objective="Review documents and risk disposition before onboarding",
			acceptance_criteria=["Documents received", "Risk reviewed", "Independent approval recorded"],
			owner_id=submitted_by,
			collaborator_ids=["approval_manager"],
			operation_graph_version=self.runtime.policy.revision_hash,
		)
		collaboration_task = self.collaboration.transition_task(
			tenant_id=TENANT_ID, project_id=PROJECT_ID, task_id=collaboration_task.id,
			actor_id=submitted_by, to_state=TaskState.WORKING, expected_revision=collaboration_task.revision,
		)
		collaboration_task = self.collaboration.transition_task(
			tenant_id=TENANT_ID, project_id=PROJECT_ID, task_id=collaboration_task.id,
			actor_id=submitted_by, to_state=TaskState.IN_REVIEW, expected_revision=collaboration_task.revision,
		)
		action = self.runtime.actions.submit(
			"connector_notifications",
			"send",
			{"vendor_record_id": vendor.id, "recipient_ref": "vendor_contact_ref", "template": "vendor_approved"},
			requester_id=submitted_by,
			idempotency_key=f"vendor_{vendor_key}_notification",
		)
		self._audit(vendor_key, "intake", submitted_by, "succeeded", {"vendor_record_id": vendor.id})
		self._audit(vendor_key, "risk_review", "agent_exception", "succeeded", dict(risk))
		return VendorCase(
			vendor_key, vendor.id, workflow.id, collaboration_task.id, human_task.id,
			action.id, action.approval_id or "", submitted_by,
		)

	def attempt_submitter_approval(self, case: VendorCase) -> None:
		"""Demonstrate both runtime and collaboration segregation boundaries."""
		self.runtime.approvals.decide(case.approval_id, actor_id=case.submitted_by, decision="approved", actor_roles={"vendor_submitter"})

	def attempt_submitter_collaboration_review(self, case: VendorCase) -> None:
		task = self.collaboration_repository.get_task(TENANT_ID, PROJECT_ID, case.collaboration_task_id)
		self.collaboration.review_task(
			tenant_id=TENANT_ID,
			project_id=PROJECT_ID,
			task_id=task.id,
			reviewer_id=case.submitted_by,
			decision=ReviewDecision.APPROVE,
			expected_revision=task.revision,
		)

	def approve(self, case: VendorCase, *, approver_id: str = "approval_manager") -> None:
		self.runtime.approvals.decide(case.approval_id, actor_id=approver_id, decision="approved", actor_roles={"approval_manager"})
		task = self.collaboration_repository.get_task(TENANT_ID, PROJECT_ID, case.collaboration_task_id)
		self.collaboration.review_task(
			tenant_id=TENANT_ID, project_id=PROJECT_ID, task_id=task.id,
			reviewer_id=approver_id, decision=ReviewDecision.APPROVE, expected_revision=task.revision,
			body="Documents and risk disposition approved",
		)
		self.runtime.human_tasks.complete(case.human_task_id, expected_revision=0)
		workflow = self.runtime.workflows.get(case.workflow_id)
		self.runtime.workflows.transition(
			workflow.id, "approved", expected_state="approval_pending", expected_revision=workflow.revision,
			idempotency_key=f"vendor_{case.vendor_key}_approved",
		)
		vendor = self.runtime.entities.get(case.vendor_record_id)
		self.runtime.entities.update(vendor.id, {**vendor.data, "status": "approved"}, expected_revision=vendor.revision)
		self._audit(case.vendor_key, "approval", approver_id, "succeeded", {"vendor_record_id": vendor.id})

	def deliver_notification(self, case: VendorCase) -> bool:
		try:
			action = self.runtime.actions.execute_approved(case.action_id)
		except ActionExecutionError as exc:
			self._audit(case.vendor_key, "notification", "system", "failed", {"error": str(exc)})
			return False
		self._mark_notified(case, action.status)
		return action.status == "succeeded"

	def retry_notification(self, case: VendorCase) -> bool:
		self.clock.advance(1)
		try:
			action = self.runtime.actions.retry(case.action_id)
		except ActionExecutionError as exc:
			self._audit(case.vendor_key, "notification_retry", "system", "failed", {"error": str(exc)})
			return False
		self._mark_notified(case, action.status)
		return action.status == "succeeded"

	def _mark_notified(self, case: VendorCase, action_status: str) -> None:
		if action_status != "succeeded":
			return
		workflow = self.runtime.workflows.get(case.workflow_id)
		if workflow.state != "notified":
			self.runtime.workflows.transition(
				workflow.id, "notified", expected_state="approved", expected_revision=workflow.revision,
				idempotency_key=f"vendor_{case.vendor_key}_notified",
			)
		vendor = self.runtime.entities.get(case.vendor_record_id)
		if vendor.data["status"] != "notified":
			self.runtime.entities.update(vendor.id, {**vendor.data, "status": "notified"}, expected_revision=vendor.revision)
		self._audit(case.vendor_key, "notification", "system", "succeeded", {"action_status": action_status})

	def summary(self, case: VendorCase) -> dict[str, Any]:
		vendor = self.runtime.entities.get(case.vendor_record_id)
		workflow = self.runtime.workflows.get(case.workflow_id)
		action = self.runtime.repository.get_action(TENANT_ID, ENVIRONMENT_ID, PROJECT_ID, case.action_id)
		return {
			"vendor_status": vendor.data["status"],
			"exception_required": vendor.data["exception_required"],
			"workflow_state": workflow.state,
			"action_status": action.status,
			"action_attempts": action.attempts,
			"notification_attempts": len(self.notifier.attempts),
			"notification_deliveries": len(self.notifier.deliveries),
			"runtime_audit_events": len(self.runtime.audit.list()),
			"runtime_telemetry_events": len(self.runtime.telemetry.list()),
			"collaboration_events": len(self.collaboration_repository.list_events(TENANT_ID, PROJECT_ID)),
		}


def run_scenario(state_root: str | Path, *, fail_first_notification: bool = False) -> dict[str, Any]:
	notifier = NotificationRecorder(failures=1 if fail_first_notification else 0)
	app = VendorOnboardingReference(state_root, notifier)
	case = app.submit_vendor("acme", name="Acme Components", risk_score=82)
	segregation = {"runtime": False, "collaboration": False}
	try:
		app.attempt_submitter_approval(case)
	except RuntimeAuthorizationError:
		segregation["runtime"] = True
	try:
		app.attempt_submitter_collaboration_review(case)
	except CollaborationAuthorizationError:
		segregation["collaboration"] = True
	app.approve(case)
	delivered = app.deliver_notification(case)
	if not delivered and fail_first_notification:
		delivered = app.retry_notification(case)
	return {**app.summary(case), "delivered": delivered, "segregation": segregation}
