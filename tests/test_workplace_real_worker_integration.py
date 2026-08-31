from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from typing import Any, Mapping

from apps.api import file_routes
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.demo.identity import AuthContext, User
from simulacra.harnesses import CodexHarness, NetworkPolicy, TerminalStatus
from simulacra.missions import JsonMissionRepository, MissionService, MissionWorker
from simulacra.missions.projections import project_attention_items, project_work_items
from simulacra.operation_graph import OperationGraphStore, load_operation_graph
from simulacra.workplace import AssignmentCoordinator


class _DeterministicLocalCodexTransport:
    """Provider-free Codex boundary used only to prove the product integration."""

    def __init__(self, *, on_run: Callable[[], None] | None = None) -> None:
        self.requests: list[Any] = []
        self.on_run = on_run

    async def create_thread(self, *, request: Any, thread_id: str | None = None) -> str:
        return thread_id or "thread_local_integration"

    async def run(self, *, request: Any, thread_id: str) -> Mapping[str, Any]:
        assert thread_id == "thread_local_integration"
        assert request.network_policy is NetworkPolicy.DENY
        if self.on_run is not None:
            self.on_run()
        self.requests.append(request)
        output = request.write_paths[0] / "codex-runtime-provider-Traceback.md"
        output.write_text("# Close report\n\nInvoice 42 requires human review.\n", encoding="utf-8")
        return {
            "status": TerminalStatus.SUCCEEDED,
            "response": "provider=openai model=internal runtime=codex path=/app/private Traceback: hidden",
            "structured_output": {"summary": "One invoice needs review."},
            "changed_files": [output],
            "events": [{"action": "artifact_written", "result": "completed"}],
            "steps": 1,
        }

    async def cancel(self, *, thread_id: str) -> None:
        return None

    async def close(self) -> None:
        return None


class _TwoAgentLocalCodexTransport:
    """Deterministic two-agent boundary with one durable handoff and one final output."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def create_thread(self, *, request: Any, thread_id: str | None = None) -> str:
        return thread_id or f"thread_two_agent_{len(self.requests) + 1}"

    async def run(self, *, request: Any, thread_id: str) -> Mapping[str, Any]:
        self.requests.append(request)
        if len(self.requests) == 1:
            return {
                "status": TerminalStatus.SUCCEEDED,
                "response": "Invoice 42 has a purchase-order mismatch requiring review.",
                "structured_output": {"handoff": "Check row 42 against the purchase order."},
                "changed_files": [],
                "events": [{"action": "research_completed", "result": "handoff_ready"}],
                "steps": 1,
            }
        output = request.write_paths[0] / "invoice-42-final-report.md"
        output.write_text(
            "# Invoice 42 final report\n\nRhea found a purchase-order mismatch. Fin confirmed the exception.\n",
            encoding="utf-8",
        )
        return {
            "status": TerminalStatus.SUCCEEDED,
            "response": "Final review pack completed.",
            "structured_output": {"summary": "Invoice 42 is ready for human verification."},
            "changed_files": [output],
            "events": [{"action": "report_written", "result": "completed"}],
            "steps": 1,
        }

    async def cancel(self, *, thread_id: str) -> None:
        return None

    async def close(self) -> None:
        return None


def _approved_revision(workspace: Path) -> str:
    store = OperationGraphStore(workspace, tenant_id="tenant_1", project_id="project_1")
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"].update({"tenant_id": "tenant_1", "project_id": "project_1"})
    revision = store.create_revision(graph, expected_revision_hash=None)
    store.approve_revision(revision.revision_hash, actor_id="owner")
    return revision.revision_hash


def test_real_mission_worker_assignment_reaches_awaiting_verification(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    collaboration_repository = JsonCollaborationRepository(tmp_path / "collaboration")
    collaboration = CollaborationService(collaboration_repository)
    collaboration.create_room(
        tenant_id="tenant_1", project_id="project_1", creator_id="owner", creator_role="owner",
    )
    mission = MissionService(JsonMissionRepository(tmp_path / "missions"))
    mission.bootstrap(
        "tenant_1", "project_1", "owner",
        {"title": "Reconcile invoices", "objective": "Find and explain invoice exceptions"},
    )
    agent = mission.add_agent("tenant_1", "project_1", {
        "name": "Fin", "role": "Reconciliation analyst", "mandate": "Prepare reviewable evidence",
        "autonomy": "execute_safely", "tools": ["artifact.write"],
    })
    revision = _approved_revision(workspace)
    coordinator = AssignmentCoordinator(
        collaboration_repository, mission, workspace,
        runs_root=tmp_path / "runs", clock=lambda: "2026-01-02T09:00:00Z",
    )
    assignment = coordinator.assign(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="owner",
        client_request_id="assign_close_report", body="@Fin reconcile invoice 42 and prepare a report",
        title="Reconcile /app/private runtime provider Traceback", objective="Return a reviewable exception report",
        acceptance_criteria=["The report cites the exception and is ready for human verification"],
        assigned_agent_ids=[agent.id], graph_revision=revision,
    )
    def assert_started_progress_is_already_visible() -> None:
        in_flight = collaboration.conversation_roots("tenant_1", "project_1")
        started_messages = [message for message in in_flight if message.kind == "agent_started"]
        assert len(started_messages) == 1
        assert started_messages[0].author == {"id": agent.id, "kind": "agent"}
        assert started_messages[0].body == "Working on the assignment. Progress and questions will return here."
        assert started_messages[0].links == {
            "work_item_id": assignment.task_id,
            "run_id": assignment.run_id,
            "output_id": None,
        }
        assert not any(message.kind == "agent_completed" for message in in_flight)

    transport = _DeterministicLocalCodexTransport(on_run=assert_started_progress_is_already_visible)

    def harness_factory(_config: Any, **adapters: Any) -> CodexHarness:
        return CodexHarness(
            transport=transport, session_repository=adapters.get("session_repository"),
        )

    result = MissionWorker(
        mission, workspace, "worker_integration", harness_factory, coordinator=coordinator,
    ).run_once("tenant_1", "project_1")

    assert result is not None and result.id == assignment.run_id and result.status == "succeeded"
    assert len(transport.requests) == 1
    outputs = mission.deliverables("tenant_1", "project_1")
    assert len(outputs) == 1 and outputs[0].state == "awaiting_verification"
    output_file_id = file_routes.output_file_id(
        outputs[0].id, tenant_id="tenant_1", project_id="project_1",
    )

    def visible_assignment(project_id: str, transaction_id: str, run_id: str):
        visible = coordinator.visible_result(
            tenant_id="tenant_1", project_id=project_id, transaction_id=transaction_id,
        )
        return visible if visible is not None and (not run_id or visible.run_id == run_id) else None

    awaiting_work = project_work_items(
        mission.repository,
        collaboration_repository,
        tenant_id="tenant_1",
        human_id="owner",
        assignment_visible=visible_assignment,
        output_file_identity=lambda _project_id, _output_id: output_file_id,
    )
    awaiting_item = next(item for item in awaiting_work if item["source_id"] == assignment.task_id)
    assert awaiting_item["state"] == "ready_for_review"
    assert awaiting_item["allowed_actions"] == ["open", "verify_output"]
    assert awaiting_item["action_targets"]["verify_output"] == {
        "kind": "output",
        "id": outputs[0].id,
        "revision": outputs[0].version,
        "file_id": output_file_id,
    }

    awaiting_attention = project_attention_items(
        mission.repository, collaboration_repository,
        tenant_id="tenant_1", human_id="owner",
    )
    output_attention = next(
        item for item in awaiting_attention
        if item["type"] == "output_verification" and item["subject_id"] == outputs[0].id
    )
    assert output_attention["actionable"] is True
    assert output_attention["allowed_actions"] == ["open", "verify_output"]

    monkeypatch.setattr(file_routes, "_mission_root", tmp_path / "missions")
    monkeypatch.setattr(file_routes, "_collaboration_root", tmp_path / "collaboration")
    monkeypatch.setattr(file_routes, "project_dir", lambda _project_id: workspace)
    file_payload = file_routes.authorized_file_inventory(
        "project_1", kind="all",
        ctx=AuthContext(
            User("owner", "owner@example.test", "Ada", "unused"),
            "tenant_1", "owner", "test",
        ),
    )
    output_file = next(item for item in file_payload["items"] if item["id"] == output_file_id)
    assert output_file["state"] == "awaiting_verification"
    assert output_file["version"] == outputs[0].version
    assert output_file["allowed_actions"] == ["verify_output"]
    assert output_file["action_targets"]["verify_output"] == {
        "kind": "output", "id": outputs[0].id, "revision": outputs[0].version,
    }

    public_projection_text = str({
        "work": awaiting_item,
        "attention": output_attention,
        "files": file_payload,
    }).lower()
    for private_term in ("codex", "runtime", "provider", "traceback", "/app/"):
        assert private_term not in public_projection_text

    messages = collaboration.conversation_roots("tenant_1", "project_1")
    agent_messages = [message for message in messages if message.kind in {"agent_started", "agent_completed"}]
    assert [message.kind for message in agent_messages] == ["agent_started", "agent_completed"]
    assert agent_messages[1].author == {"id": agent.id, "kind": "agent"}
    assert agent_messages[1].body == "Work completed. An output is ready for human verification."
    for private_detail in ("provider", "model", "runtime", "codex", "/app/", "Traceback"):
        assert private_detail not in " ".join(message.body or "" for message in agent_messages)
    assert agent_messages[1].links == {
        "work_item_id": assignment.task_id,
        "run_id": assignment.run_id,
        "output_id": outputs[0].id,
    }

    verified = mission.verify_deliverable(
        "tenant_1", "project_1", outputs[0].id, "owner",
        outputs[0].content_hash, outputs[0].revision,
    )
    assert verified.state == "verified"
    assert verified.verified_by == "owner"
    assert verified.verified_hash == outputs[0].content_hash

    verified_work = project_work_items(
        mission.repository,
        collaboration_repository,
        tenant_id="tenant_1",
        human_id="owner",
        assignment_visible=visible_assignment,
        output_file_identity=lambda _project_id, _output_id: output_file_id,
    )
    verified_item = next(item for item in verified_work if item["source_id"] == assignment.task_id)
    assert verified_item["state"] == "done"
    assert "verify_output" not in verified_item["allowed_actions"]

    verified_attention = project_attention_items(
        mission.repository, collaboration_repository,
        tenant_id="tenant_1", human_id="owner",
    )
    closed_output_attention = next(
        item for item in verified_attention
        if item["type"] == "output_verification" and item["subject_id"] == outputs[0].id
    )
    assert closed_output_attention["actionable"] is False
    assert closed_output_attention["allowed_actions"] == ["open"]

    coordinator.project_agent_results("tenant_1", "project_1")
    coordinator.project_agent_results("tenant_1", "project_1")
    replayed = collaboration.conversation_roots("tenant_1", "project_1")
    assert [message.id for message in replayed] == [message.id for message in messages]


def test_two_agent_assignment_hands_off_in_order_and_returns_one_crew_output(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    collaboration_repository = JsonCollaborationRepository(tmp_path / "collaboration")
    collaboration = CollaborationService(collaboration_repository)
    collaboration.create_room(
        tenant_id="tenant_1", project_id="project_1", creator_id="owner", creator_role="owner",
    )
    mission = MissionService(JsonMissionRepository(tmp_path / "missions"))
    mission.bootstrap(
        "tenant_1", "project_1", "owner",
        {"title": "Reconcile invoices", "objective": "Find and verify invoice exceptions"},
    )
    researcher = mission.add_agent("tenant_1", "project_1", {
        "name": "Rhea", "role": "Researcher", "mandate": "Find source-grounded exceptions",
        "autonomy": "assist", "tools": ["artifact.write"],
    })
    reviewer = mission.add_agent("tenant_1", "project_1", {
        "name": "Fin", "role": "Reviewer", "mandate": "Turn the handoff into reviewable evidence",
        "autonomy": "execute_safely", "tools": ["artifact.write"],
    })
    revision = _approved_revision(workspace)
    coordinator = AssignmentCoordinator(
        collaboration_repository, mission, workspace,
        runs_root=tmp_path / "runs", clock=lambda: "2026-01-02T09:00:00Z",
    )
    assignment = coordinator.assign(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="owner",
        client_request_id="assign_two_agent_close_report",
        body="@Rhea @Fin reconcile invoice 42 and prepare a final report",
        title="Reconcile invoice 42", objective="Return one reviewable exception report",
        acceptance_criteria=["Both agents contribute in order and the final report is ready for human verification"],
        assigned_agent_ids=[researcher.id, reviewer.id], graph_revision=revision,
    )
    transport = _TwoAgentLocalCodexTransport()
    worker = MissionWorker(
        mission, workspace, "worker_integration",
        lambda _config, **adapters: CodexHarness(
            transport=transport, session_repository=adapters.get("session_repository"),
        ),
        coordinator=coordinator,
    )

    after_research = worker.run_once("tenant_1", "project_1")
    assert after_research is not None and after_research.status == "queued", (
        after_research.error if after_research is not None else None,
        mission.events("tenant_1", "project_1"),
        [request.prompt for request in transport.requests],
    )
    assert after_research.completed_agent_ids == [researcher.id]
    completed = worker.run_once("tenant_1", "project_1")
    assert completed is not None and completed.status == "succeeded"
    assert completed.completed_agent_ids == [researcher.id, reviewer.id]
    assert "Agent: Rhea (Researcher)" in transport.requests[0].prompt
    assert "Agent: Fin (Reviewer)" in transport.requests[1].prompt
    assert "No previous crew output" in transport.requests[0].prompt
    assert "Rhea" in transport.requests[1].prompt
    assert "purchase-order mismatch" in transport.requests[1].prompt

    messages = collaboration.conversation_roots("tenant_1", "project_1")
    milestones = [message for message in messages if message.kind in {"agent_started", "agent_completed"}]
    assert [(message.author["id"], message.kind) for message in milestones] == [
        (researcher.id, "agent_started"),
        (researcher.id, "agent_completed"),
        (reviewer.id, "agent_started"),
        (reviewer.id, "agent_completed"),
    ]
    assert milestones[1].body == "Handoff completed. Fin can continue."
    assert milestones[3].body == "Work completed. An output is ready for human verification."

    outputs = mission.deliverables("tenant_1", "project_1")
    assert len(outputs) == 1
    final_output = outputs[0]
    assert final_output.producer_id == reviewer.id and final_output.state == "awaiting_verification"
    monkeypatch.setattr(file_routes, "_mission_root", tmp_path / "missions")
    monkeypatch.setattr(file_routes, "_collaboration_root", tmp_path / "collaboration")
    monkeypatch.setattr(file_routes, "project_dir", lambda _project_id: workspace)
    file_id = file_routes.output_file_id(final_output.id, tenant_id="tenant_1", project_id="project_1")
    metadata = file_routes.file_metadata(
        "project_1", file_id,
        ctx=AuthContext(User("owner", "owner@example.test", "Ada", "unused"), "tenant_1", "owner", "test"),
    )["file"]
    assert metadata["contributors"] == [
        {"id": researcher.id, "display_name": "Rhea"},
        {"id": reviewer.id, "display_name": "Fin"},
    ]
    verified = mission.verify_deliverable(
        "tenant_1", "project_1", final_output.id, "owner",
        final_output.content_hash, final_output.revision,
    )
    assert verified.state == "verified"


def test_agent_execution_waits_until_start_progress_is_visible(tmp_path: Path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    collaboration_repository = JsonCollaborationRepository(tmp_path / "collaboration")
    collaboration = CollaborationService(collaboration_repository)
    collaboration.create_room(
        tenant_id="tenant_1", project_id="project_1", creator_id="owner", creator_role="owner",
    )
    mission = MissionService(JsonMissionRepository(tmp_path / "missions"))
    mission.bootstrap(
        "tenant_1", "project_1", "owner",
        {"title": "Reconcile invoices", "objective": "Find invoice exceptions"},
    )
    agent = mission.add_agent("tenant_1", "project_1", {
        "name": "Fin", "role": "Reconciliation analyst", "mandate": "Prepare reviewable evidence",
        "autonomy": "execute_safely", "tools": ["artifact.write"],
    })
    revision = _approved_revision(workspace)
    coordinator = AssignmentCoordinator(
        collaboration_repository, mission, workspace,
        runs_root=tmp_path / "runs", clock=lambda: "2026-01-02T09:00:00Z",
    )
    assignment = coordinator.assign(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="owner",
        client_request_id="assign_visible_start", body="@Fin reconcile invoice 42",
        title="Reconcile invoice 42", objective="Return a reviewable report",
        acceptance_criteria=["The report is ready for human verification"],
        assigned_agent_ids=[agent.id], graph_revision=revision,
    )
    original_project = coordinator.project_agent_results

    def unavailable_after_start(tenant_id: str, project_id: str) -> list[str]:
        if any(event["type"] == "agent_started" for event in mission.events(tenant_id, project_id)):
            raise OSError("temporary collaboration outage")
        return original_project(tenant_id, project_id)

    monkeypatch.setattr(coordinator, "project_agent_results", unavailable_after_start)
    transport = _DeterministicLocalCodexTransport()
    result = MissionWorker(
        mission, workspace, "worker_integration",
        lambda _config, **adapters: CodexHarness(
            transport=transport, session_repository=adapters.get("session_repository"),
        ),
        coordinator=coordinator,
    ).run_once("tenant_1", "project_1")

    assert result is not None and result.id == assignment.run_id and result.status == "failed"
    assert transport.requests == []
    assert not any(message.kind == "agent_started" for message in collaboration.conversation_roots("tenant_1", "project_1"))

    monkeypatch.setattr(coordinator, "project_agent_results", original_project)
    original_project("tenant_1", "project_1")
    repaired = collaboration.conversation_roots("tenant_1", "project_1")
    repaired_agent = [message for message in repaired if message.author == {"id": agent.id, "kind": "agent"}]
    assert [message.kind for message in repaired_agent] == ["agent_started", "agent_progress"]
    assert repaired_agent[1].body == "Work stopped before completion. Review it in Work before continuing."
    assert repaired_agent[1].links == {
        "work_item_id": assignment.task_id,
        "run_id": assignment.run_id,
        "output_id": None,
    }
