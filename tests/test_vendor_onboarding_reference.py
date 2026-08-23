from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from simulacra.collaboration import AuthorizationError as CollaborationAuthorizationError
from simulacra.operation_graph import validate_operation_graph
from simulacra.runtime import RuntimeAuthorizationError

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_DIR = ROOT / "examples" / "vendor-onboarding"


def load_reference():
	spec = importlib.util.spec_from_file_location("vendor_onboarding_reference", REFERENCE_DIR / "reference.py")
	assert spec and spec.loader
	module = importlib.util.module_from_spec(spec)
	sys.modules[spec.name] = module
	spec.loader.exec_module(module)
	return module


def test_reference_operation_graph_is_valid_and_contains_no_agent_or_secret_bypass():
	graph = json.loads((REFERENCE_DIR / "operation-graph.json").read_text(encoding="utf-8"))
	assert validate_operation_graph(graph) == graph
	agent = graph["agents"][0]
	assert agent["actor_type"] == "runtime_agent"
	assert all(term not in " ".join([*agent["capabilities"], *agent["tools"]]).lower() for term in ("source", "filesystem", "shell", "process"))
	configuration = graph["connectors"][0]["configuration"]
	assert configuration and all(key.endswith("_ref") for key in configuration)
	assert not any(key in json.dumps(graph).lower() for key in ('"password"', '"access_token"', '"client_secret"'))


def test_success_scenario_enforces_segregation_and_completes_every_plane(tmp_path: Path):
	reference = load_reference()
	result = reference.run_scenario(tmp_path)
	assert result == {
		"vendor_status": "notified",
		"exception_required": True,
		"workflow_state": "notified",
		"action_status": "succeeded",
		"action_attempts": 1,
		"notification_attempts": 1,
		"notification_deliveries": 1,
		"runtime_audit_events": 4,
		"runtime_telemetry_events": 4,
		"collaboration_events": 8,
		"delivered": True,
		"segregation": {"runtime": True, "collaboration": True},
	}


def test_submitter_cannot_approve_runtime_or_collaboration_review(tmp_path: Path):
	reference = load_reference()
	app = reference.VendorOnboardingReference(tmp_path, reference.NotificationRecorder())
	case = app.submit_vendor("segregation", name="Segregated Supplies", risk_score=25)
	with pytest.raises(RuntimeAuthorizationError, match="self-approve"):
		app.attempt_submitter_approval(case)
	with pytest.raises(CollaborationAuthorizationError, match="own work"):
		app.attempt_submitter_collaboration_review(case)
	assert app.runtime.approvals.get(case.approval_id).status == "pending"
	assert app.collaboration_repository.get_task(reference.TENANT_ID, reference.PROJECT_ID, case.collaboration_task_id).state.value == "in_review"


def test_notification_failure_retries_durably_with_one_external_delivery(tmp_path: Path):
	reference = load_reference()
	result = reference.run_scenario(tmp_path, fail_first_notification=True)
	assert result["delivered"] is True
	assert result["vendor_status"] == result["workflow_state"] == "notified"
	assert result["action_status"] == "succeeded"
	assert result["action_attempts"] == result["notification_attempts"] == 2
	assert result["notification_deliveries"] == 1
	assert result["runtime_audit_events"] == result["runtime_telemetry_events"] == 5


def test_durable_state_reopens_without_control_plane_service(tmp_path: Path):
	reference = load_reference()
	notifier = reference.NotificationRecorder()
	app = reference.VendorOnboardingReference(tmp_path, notifier)
	case = app.submit_vendor("restart", name="Restartable Vendor", risk_score=72)
	app.approve(case)
	assert app.deliver_notification(case)

	reopened = reference.VendorOnboardingReference(tmp_path, reference.NotificationRecorder())
	assert reopened.runtime.entities.get(case.vendor_record_id).data["status"] == "notified"
	assert reopened.runtime.workflows.get(case.workflow_id).state == "notified"
	assert len(reopened.runtime.entities.query("entity_document")) == 2
	assert reopened.runtime.repository.get_action(reference.TENANT_ID, reference.ENVIRONMENT_ID, reference.PROJECT_ID, case.action_id).status == "succeeded"
	assert len(reopened.runtime.audit.list()) == 4
	assert len(reopened.collaboration_repository.list_events(reference.TENANT_ID, reference.PROJECT_ID)) == 8
	assert reopened.runtime.health.readiness()["status"] == "ready"
