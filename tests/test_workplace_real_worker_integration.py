from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.harnesses import CodexHarness, NetworkPolicy, TerminalStatus
from simulacra.missions import JsonMissionRepository, MissionService, MissionWorker
from simulacra.operation_graph import OperationGraphStore, load_operation_graph
from simulacra.workplace import AssignmentCoordinator


class _DeterministicLocalCodexTransport:
    """Provider-free Codex boundary used only to prove the product integration."""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def create_thread(self, *, request: Any, thread_id: str | None = None) -> str:
        return thread_id or "thread_local_integration"

    async def run(self, *, request: Any, thread_id: str) -> Mapping[str, Any]:
        assert thread_id == "thread_local_integration"
        assert request.network_policy is NetworkPolicy.DENY
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


def _approved_revision(workspace: Path) -> str:
    store = OperationGraphStore(workspace, tenant_id="tenant_1", project_id="project_1")
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"].update({"tenant_id": "tenant_1", "project_id": "project_1"})
    revision = store.create_revision(graph, expected_revision_hash=None)
    store.approve_revision(revision.revision_hash, actor_id="owner")
    return revision.revision_hash


def test_real_mission_worker_assignment_reaches_awaiting_verification(tmp_path: Path):
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
    transport = _DeterministicLocalCodexTransport()

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

    messages = collaboration.conversation_roots("tenant_1", "project_1")
    agent_messages = [message for message in messages if message.kind == "agent_completed"]
    assert len(agent_messages) == 1
    assert agent_messages[0].author == {"id": agent.id, "kind": "agent"}
    assert agent_messages[0].body == "Work completed. An output is ready for human verification."
    for private_detail in ("provider", "model", "runtime", "codex", "/app/", "Traceback"):
        assert private_detail not in agent_messages[0].body
    assert agent_messages[0].links == {
        "work_item_id": assignment.task_id,
        "run_id": assignment.run_id,
        "output_id": outputs[0].id,
    }

    coordinator.project_agent_results("tenant_1", "project_1")
    coordinator.project_agent_results("tenant_1", "project_1")
    replayed = collaboration.conversation_roots("tenant_1", "project_1")
    assert [message.id for message in replayed] == [message.id for message in messages]
