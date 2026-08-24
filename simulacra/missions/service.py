"""Mission V0 domain service; all writes use repository transactions."""

from __future__ import annotations

import json
import os
import re
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from .models import (
    PROFILES, AgentDefinition, AutomationTrigger, Deliverable, Mission,
    MissionRun, clean_public_mapping, condition_matches, effective_budget, hash_artifact, normalize_budget,
    new_id, next_cron_due, now,
)
from .repository import JsonMissionRepository, MissionConflictError, MissionNotFoundError

ACTIVE_RUN_STATUSES = {"queued", "preparing", "running", "awaiting_approval", "verifying"}
# This deliberately mirrors models._SECRET_VALUE, which rejects public Mission
# inputs. Provider output is untrusted too: redact it before events, results, or
# trajectory exports can persist it. Query-key variants are common in URLs and
# must include both underscore and hyphen spellings.
_SECRET_VALUE = re.compile(
    r"(?:-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----[\s\S]*?-----END(?: [A-Z]+)? PRIVATE KEY-----|"
    r"(?:sk(?:-|_live_|_test_)|ghp_|github_pat_|xox[abp]-|npm_)[A-Za-z0-9_-]{8,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|Bearer\s+[A-Za-z0-9._~-]{8,}|"
    r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"https?://[^\s/@]+:[^\s/@]+@|"
    r"[?&](?:access[_-]?token|api[_-]?key|token|key|secret|password|authorization|credential)=[^&\s]+|"
    r"(?:api[_-]?key|access[_-]?token|token|secret|password|authorization)\s*[:=]\s*[^\s,;)}\]]+)",
    re.I,
)
_SECRET_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?token|token|key|secret|password|authorization|credential)",
    re.I,
)
_MISSION_TOOLS = frozenset({"document.read", "code.read", "artifact.write", "code.write"})
EVENT_RETENTION = 2000
APPROVAL_RETENTION = 500
OVERVIEW_RETENTION = 100
TRIGGER_OCCURRENCE_RETENTION = 128
RUN_HISTORY_RETENTION = 256
AUTOMATIC_ACTIVE_RUN_LIMIT = 128


def _safe_value(value: Any, depth: int = 0) -> Any:
    """Bounded durable telemetry; credential-looking content is never retained."""
    if depth > 6:
        return "[truncated]"
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[redacted]", value)[:8000]
    if isinstance(value, Mapping):
        return {str(key)[:128]: _safe_value(item, depth + 1) for key, item in list(value.items())[:80]
                if not _SECRET_KEY.search(str(key))}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item, depth + 1) for item in list(value)[:80]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:512]


class MissionService:
    def __init__(self, repository: JsonMissionRepository):
        self.repository = repository

    @staticmethod
    def _profile(name: str = "balanced") -> dict[str, Any]:
        profile = name if name in PROFILES else "balanced"
        return {
            "runtime": "codex",
            "profile": profile,
            "model": os.getenv(f"CMUL8_MISSION_{profile.upper()}_MODEL", os.getenv("CMUL8_MODEL", "default")),
            "reasoning_effort": os.getenv(
                f"CMUL8_MISSION_{profile.upper()}_REASONING",
                os.getenv("CMUL8_MODEL_REASONING_EFFORT", ""),
            ) or None,
            "codex_profile": os.getenv("CMUL8_CODEX_PROFILE") or None,
        }

    @staticmethod
    def _mission(records: dict[str, Any]) -> Mission:
        if records["mission"] is None:
            raise MissionNotFoundError("mission not found")
        return Mission.from_dict(records["mission"])

    def bootstrap(self, tenant_id: str, project_id: str, owner_id: str, data: Mapping[str, Any]) -> Mission:
        clean_public_mapping(data)

        def mutate(records: dict[str, Any]) -> Mission:
            if records["mission"] is not None:
                return Mission.from_dict(records["mission"])
            title = str(data.get("title") or "Mission").strip() or "Mission"
            objective = str(data.get("objective") or "").strip() or title
            definition_of_done = str(data.get("definition_of_done") or "").strip() or (
                "Produce the requested outcome from the approved sources and workflow, "
                "then obtain human verification of the exact final deliverable."
            )
            mission = Mission(
                id=new_id("mission"), tenant_id=tenant_id, project_id=project_id,
                owner_id=owner_id, title=title,
                objective=objective,
                definition_of_done=definition_of_done,
                template=str(data.get("template") or "custom"),
                verifier_ids=list(data.get("verifier_ids") or [owner_id]),
                priority=str(data.get("priority") or "normal"),
                risk_level=str(data.get("risk_level") or "medium"),
                deadline=data.get("deadline"), budget=normalize_budget(data.get("budget")),
            )
            records["mission"] = mission.to_dict()
            return mission

        return self.repository.mutate(tenant_id, project_id, mutate)

    def mission(self, tenant_id: str, project_id: str) -> Mission:
        return Mission.from_dict(self.repository.get_mission(tenant_id, project_id))

    def update_mission(self, tenant_id: str, project_id: str, patch: Mapping[str, Any], expected_revision: int) -> Mission:
        clean_public_mapping(patch)

        def mutate(records: dict[str, Any]) -> Mission:
            mission = self._mission(records)
            if mission.revision != expected_revision:
                raise MissionConflictError("stale mission revision")
            for key in (
                "title", "objective", "definition_of_done", "template", "verifier_ids", "status",
                "priority", "risk_level", "deadline", "budget",
            ):
                if key in patch:
                    setattr(mission, key, normalize_budget(patch[key]) if key == "budget" else patch[key])
            mission.revision += 1
            mission.updated_at = now()
            mission.__post_init__()
            records["mission"] = mission.to_dict()
            return mission

        return self.repository.mutate(tenant_id, project_id, mutate)

    def _records(self, tenant_id: str, project_id: str, name: str, cls: type[Any]) -> list[Any]:
        return [cls.from_dict(value) for _, value in sorted(self.repository.list_collection(tenant_id, project_id, name).items())]

    def agents(self, tenant_id: str, project_id: str) -> list[AgentDefinition]:
        return sorted(self._records(tenant_id, project_id, "agents", AgentDefinition), key=lambda agent: (agent.created_at, agent.id))

    def add_agent(self, tenant_id: str, project_id: str, data: Mapping[str, Any]) -> AgentDefinition:
        clean_public_mapping(data)
        tools = list(data.get("tools") or [])
        scopes = list(data.get("data_scope") or [])
        if any(not isinstance(item, str) or item not in _MISSION_TOOLS for item in tools):
            raise ValueError("Mission agent tools must be from the approved allowlist")
        if any(not isinstance(item, str) or not item or item.startswith("/") or "\\" in item or (len(item) > 1 and item[1] == ":") or any(
            part in {"", ".", "..", ".codex", ".cmul8", ".mission-control", "audit", "control"} or any(ord(char) < 32 for char in part)
            for part in item.split("/")
        ) for item in scopes):
            raise ValueError("Mission data scope must be a safe relative path")

        def mutate(records: dict[str, Any]) -> AgentDefinition:
            mission = self._mission(records)
            agent = AgentDefinition(
                id=new_id("agent"), tenant_id=tenant_id, project_id=project_id, mission_id=mission.id,
                name=str(data["name"]), role=str(data["role"]), mandate=str(data["mandate"]),
                responsibilities=list(data.get("responsibilities") or []), data_scope=scopes,
                tools=tools, autonomy=str(data.get("autonomy") or "assist"),
                escalation_actor_id=data.get("escalation_actor_id"), budget=normalize_budget(data.get("budget")),
            )
            records["agents"][agent.id] = agent.to_dict()
            return agent

        return self.repository.mutate(tenant_id, project_id, mutate)

    def runs(self, tenant_id: str, project_id: str) -> list[MissionRun]:
        return sorted(self._records(tenant_id, project_id, "runs", MissionRun), key=lambda run: (run.created_at, run.id))

    def events(self, tenant_id: str, project_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        values = sorted((dict(value) for value in self.repository.list_collection(tenant_id, project_id, "events").values()), key=lambda value: (str(value.get("timestamp", "")), str(value.get("id", ""))))
        return values[-limit:] if limit else values

    def approvals(self, tenant_id: str, project_id: str) -> list[dict[str, Any]]:
        """Return an overview where actionable approvals are never hidden.

        Storage itself is capped at ``APPROVAL_RETENTION``. Within that bound,
        retain every pending/approved item (even if it is old), then use any
        remaining response capacity for the newest closed history.
        """
        values = [dict(value) for value in self.repository.list_collection(tenant_id, project_id, "approvals").values()]
        sort_key = lambda value: (str(value.get("updated_at") or value.get("created_at") or ""), str(value.get("id") or ""))
        active = [value for value in values if value.get("status") in {"pending", "approved"}]
        closed = [value for value in values if value.get("status") not in {"pending", "approved"}]
        capacity = max(0, APPROVAL_RETENTION - len(active))
        return sorted([*active, *sorted(closed, key=sort_key)[-capacity:]], key=sort_key)

    def approval(self, tenant_id: str, project_id: str, approval_id: str) -> dict[str, Any] | None:
        """Read exactly one durable approval for execution gating, never an overview."""
        return self.repository.get_collection_item(tenant_id, project_id, "approvals", approval_id)

    def trajectory_page(self, tenant_id: str, project_id: str, cursor: str | None, limit: int) -> dict[str, Any]:
        values = self.events(tenant_id, project_id); start = 0
        if cursor:
            matching = next((index for index, event in enumerate(values) if event.get("id") == cursor), None)
            if matching is None: raise ValueError("invalid or expired trajectory cursor")
            start = matching + 1
        page = values[start:start + max(1, min(limit, 500))]
        next_cursor = str(page[-1]["id"]) if start + len(page) < len(values) and page else None
        dropped = int(self.repository.retention(tenant_id, project_id).get("dropped_events", 0))
        return {"events": page, "next_cursor": next_cursor, "retention": {"events": EVENT_RETENTION, "retained": len(values), "dropped_events": dropped, "truncated": dropped > 0}}

    def trajectory_export(self, tenant_id: str, project_id: str, *, include_events: bool = True) -> dict[str, Any]:
        export = {"schema_version": 1, "mission": self.mission(tenant_id, project_id).to_dict(),
            "agents": [item.to_dict() for item in self.agents(tenant_id, project_id)],
            "runs": [item.to_dict() for item in self.runs(tenant_id, project_id)], "approvals": self.approvals(tenant_id, project_id),
            "deliverables": [item.to_dict() for item in self.deliverables(tenant_id, project_id)]}
        if include_events: export["events"] = self.events(tenant_id, project_id)
        return _safe_value(export)

    def finalize_recovered_run(self, tenant_id: str, project_id: str, run_id: str, worker_id: str) -> MissionRun:
        def mutate(records: dict[str, Any]) -> MissionRun:
            run = MissionRun.from_dict(records["runs"][run_id])
            agents = [row for _, row in sorted(records["agents"].items(), key=lambda item: (item[1].get("created_at", ""), item[0]))]
            if run.status != "running" or run.lease_owner != worker_id or run.next_agent_position != len(agents) or len(run.completed_agent_ids) != len(agents):
                raise MissionConflictError("run is not safely finalizable")
            run.status = "succeeded"; run.completed_at = now(); run.result = {"status": "succeeded"}; run.current_agent_id = None; run.invocation_id = run.invocation_started_at = None; run.lease_owner = run.lease_until = None
            self._touch(run); self._event(records, run, "run_finalized", {}); records["runs"][run.id] = run.to_dict(); return run
        return self.repository.mutate(tenant_id, project_id, mutate)

    @staticmethod
    def _touch(run: MissionRun) -> None:
        run.revision += 1; run.updated_at = now()

    @staticmethod
    def _close_pending(records: dict[str, Any], run: MissionRun, reason: str) -> None:
        for approval in records["approvals"].values():
            if approval.get("run_id") == run.id and approval.get("status") in {"pending", "approved"}:
                approval["status"] = "superseded"; approval["superseded_reason"] = reason
                approval["revision"] = int(approval.get("revision", 0)) + 1; approval["updated_at"] = now()
        run.active_approval_id = None

    @staticmethod
    def _cap_approvals(records: dict[str, Any]) -> None:
        """Never evict an actionable approval merely to satisfy retention."""
        approvals = records["approvals"]
        stale = sorted((item for item in approvals.items() if item[1].get("status") not in {"pending", "approved"}), key=lambda item: (str(item[1].get("updated_at", "")), item[0]))
        for approval_id, _ in stale:
            if len(approvals) <= APPROVAL_RETENTION:
                break
            approvals.pop(approval_id, None)
        if len(approvals) > APPROVAL_RETENTION:
            raise MissionConflictError("approval retention quota reached")

    @staticmethod
    def _event(records: dict[str, Any], run: MissionRun, kind: str, payload: Mapping[str, Any] | None = None) -> None:
        event_id = new_id("trajectory")
        records["events"][event_id] = {"id": event_id, "run_id": run.id, "mission_id": run.mission_id,
            "type": kind, "timestamp": now(), "correlation_id": run.invocation_id or run.id,
            "payload": _safe_value(payload or {})}
        stale = sorted(records["events"].items(), key=lambda item: (str(item[1].get("timestamp", "")), item[0]))[:-EVENT_RETENTION]
        for old, _ in stale: records["events"].pop(old, None)
        if stale: records.setdefault("retention", {}).update({"dropped_events": int(records.setdefault("retention", {}).get("dropped_events", 0)) + len(stale)})

    def claim_next(self, tenant_id: str, project_id: str, worker_id: str, lease_seconds: int = 900) -> MissionRun | None:
        """Atomically claim one safe queued run. Expired started work is uncertain."""
        def mutate(records: dict[str, Any]) -> MissionRun | None:
            self._mission(records); point = datetime.now(UTC)
            for raw in records["runs"].values():
                run = MissionRun.from_dict(raw)
                if run.status == "running" and run.lease_until and datetime.fromisoformat(run.lease_until) <= point:
                    if run.invocation_started_at:
                        run.status = "awaiting_approval"; run.lease_owner = run.lease_until = None
                        run.error = {"code": "recovery_retry", "message": "A previous Codex turn may have started; human retry required."}
                        if not run.active_approval_id:
                            approval_id = new_id("approval")
                            records["approvals"][approval_id] = {"id": approval_id, "run_id": run.id, "agent_id": run.current_agent_id,
                                "code": "recovery_retry", "message": run.error["message"], "status": "pending", "revision": 1, "created_at": now(), "updated_at": now()}
                            run.active_approval_id = approval_id
                            self._cap_approvals(records)
                        self._touch(run); self._event(records, run, "recovery_required", run.error); records["runs"][run.id] = run.to_dict()
                    else:
                        run.status = "queued"; run.lease_owner = run.lease_until = None; self._touch(run); records["runs"][run.id] = run.to_dict()
            # Repository mutation is the cross-worker coordination boundary.
            # Do not let another replica claim a second run while this Mission
            # has any live worker-owned execution; process-local locks cannot
            # enforce this with multiple worker pods. Expired executions were
            # reconciled above before this fail-closed check.
            mission_id = self._mission(records).id
            if any(
                run.mission_id == mission_id and run.status == "running"
                for run in (MissionRun.from_dict(raw) for raw in records["runs"].values())
            ):
                return None
            for raw in records["runs"].values():
                run = MissionRun.from_dict(raw)
                if run.status != "queued": continue
                run.status = "running"; run.started_at = run.started_at or now(); run.lease_owner = worker_id
                run.lease_until = (point + timedelta(seconds=max(30, lease_seconds))).isoformat(); run.error = None
                self._touch(run); self._event(records, run, "run_claimed", {"worker": worker_id})
                records["runs"][run.id] = run.to_dict(); return run
            return None
        return self.repository.mutate(tenant_id, project_id, mutate)

    def gate(self, tenant_id: str, project_id: str, run_id: str, code: str, message: str, *, lease_owner: str | None = None, agent_id: str | None = None) -> MissionRun:
        def mutate(records: dict[str, Any]) -> MissionRun:
            run = MissionRun.from_dict(records["runs"][run_id])
            if lease_owner and run.lease_owner != lease_owner: raise MissionConflictError("lease is no longer owned")
            actionable = code in {"checkpoint_required", "recovery_retry"}
            approval_id = new_id("approval") if actionable else None
            if actionable:
                self._close_pending(records, run, "replaced")
                records["approvals"][approval_id] = {"id": approval_id, "run_id": run.id, "agent_id": agent_id,
                    "code": code, "message": message[:500], "status": "pending", "revision": 1, "created_at": now(), "updated_at": now()}
                self._cap_approvals(records)
            run.status = "awaiting_approval"; run.lease_owner = run.lease_until = None
            run.active_approval_id = approval_id
            run.error = {"code": code, "message": message[:500]}; self._touch(run); self._event(records, run, "gate", run.error)
            records["runs"][run.id] = run.to_dict(); return run
        return self.repository.mutate(tenant_id, project_id, mutate)

    @staticmethod
    def _binding_digest(value: Mapping[str, Any]) -> str:
        return hashlib.sha256(json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    def mark_agent_started(self, tenant_id: str, project_id: str, run_id: str, agent_id: str, worker_id: str,
                           prompt: str = "", binding: Mapping[str, Any] | None = None) -> MissionRun:
        def mutate(records: dict[str, Any]) -> MissionRun:
            run = MissionRun.from_dict(records["runs"][run_id])
            if run.status != "running" or run.lease_owner != worker_id: raise MissionConflictError("lease is no longer owned")
            if run.active_approval_id:
                approval = records["approvals"].get(run.active_approval_id)
                if not approval or approval.get("status") != "approved" or approval.get("agent_id") != agent_id:
                    raise MissionConflictError("active checkpoint approval is required")
                approval["status"] = "consumed"; approval["revision"] += 1; approval["updated_at"] = now(); run.active_approval_id = None
            agent = records["agents"].get(agent_id)
            if not isinstance(agent, Mapping) or agent.get("mission_id") != run.mission_id:
                raise MissionConflictError("Mission agent is no longer valid")
            # The worker supplies the immutable admission snapshot.  Rebuild the
            # values that are owned by Mission state rather than trusting caller
            # input, then reject a mismatched/tampered snapshot before an
            # invocation marker is persisted.
            expected = {
                "operation_graph_hash": run.contract_revision,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "role": f"mission:{run.mission_id}:agent:{agent_id}",
                "tools": list(agent.get("tools") or []),
                "autonomy": agent.get("autonomy"),
                "execution_profile": run.execution_profile,
                "effective_budget": effective_budget(self._mission(records).budget, agent.get("budget")),
            }
            supplied = dict(binding or {})
            if binding is not None:
                required = {"operation_graph_revision", *expected}
                if set(supplied) != required or not isinstance(supplied.get("operation_graph_revision"), int) or supplied["operation_graph_revision"] <= 0:
                    raise MissionConflictError("invalid Mission execution binding")
                if any(supplied[key] != value for key, value in expected.items()):
                    raise MissionConflictError("Mission execution binding changed")
            else:
                # Compatibility for historical/direct callers; production worker
                # always sends the complete graph-revision binding above.
                supplied = {"operation_graph_revision": 0, **expected}
            run.current_agent_id = agent_id; run.invocation_id = new_id("invocation"); run.invocation_started_at = now()
            supplied["invocation_id"] = run.invocation_id
            run.execution_binding = supplied
            run.progress = {"current_agent_id": agent_id, "completed": len(run.completed_agent_ids)}
            self._touch(run); self._event(records, run, "agent_started", {
                "agent_id": agent_id, "prompt": prompt,
                "effective_budget": supplied["effective_budget"],
                "binding_sha256": self._binding_digest(supplied),
            })
            records["runs"][run.id] = run.to_dict(); return run
        return self.repository.mutate(tenant_id, project_id, mutate)

    def record_result(self, tenant_id: str, project_id: str, run_id: str, worker_id: str, agent_id: str,
                      result: Mapping[str, Any], artifacts: list[Mapping[str, Any]]) -> MissionRun:
        def mutate(records: dict[str, Any]) -> MissionRun:
            run = MissionRun.from_dict(records["runs"][run_id])
            if run.status != "running" or run.lease_owner != worker_id or run.current_agent_id != agent_id:
                raise MissionConflictError("lease is no longer owned")
            status = str(result.get("status", "failed"))
            session_id = result.get("session_id")
            if isinstance(session_id, str): run.session_ids[agent_id] = session_id

            def persist_artifacts(*, failed_run: bool) -> None:
                """Keep observed files immutable and explicitly unverified.

                The worker supplies descriptor-read evidence only after
                confining each changed path to the agent's write roots. This
                service still rejects malformed references so a direct caller
                cannot turn a failed provider response into arbitrary durable
                deliverable metadata.
                """
                for artifact in artifacts:
                    evidence = _safe_value(artifact)
                    if not isinstance(evidence, Mapping):
                        raise MissionConflictError("invalid Mission artifact evidence")
                    artifact_ref = evidence.get("artifact_ref")
                    digest = evidence.get("sha256")
                    reference = Path(artifact_ref) if isinstance(artifact_ref, str) else None
                    if (
                        reference is None or reference.is_absolute() or not reference.parts
                        or "\\" in artifact_ref
                        or any(part in {"", ".", ".."} or any(ord(char) < 32 for char in part) for part in reference.parts)
                        or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
                    ):
                        raise MissionConflictError("invalid Mission artifact evidence")
                    captured = dict(evidence)
                    if failed_run:
                        # A candidate from an interrupted/failed turn is never
                        # indistinguishable from a successful delivery.
                        captured.update({"run_id": run.id, "run_status": "failed"})
                    name = reference.name[:200]
                    older = sorted(
                        (Deliverable.from_dict(row) for row in records["deliverables"].values() if row.get("name") == name),
                        key=lambda item: item.version,
                    )
                    suffix = reference.suffix.lower()
                    kind = "report" if suffix in {".md", ".txt", ".pdf"} else "dataset" if suffix in {".csv", ".json", ".parquet"} else "visualization" if suffix in {".svg", ".png", ".jpg"} else "application" if suffix in {".html"} else "code"
                    item = Deliverable(
                        id=new_id("deliverable"), tenant_id=tenant_id, project_id=project_id,
                        mission_id=run.mission_id, type=kind, name=name, producer_id=agent_id,
                        version=max((old.version for old in older), default=0) + 1,
                        content_hash=digest,
                        source_ref=(f"mission/run/{run.id}/failed-agent/{agent_id}" if failed_run else "mission/agent"),
                        artifact_ref=artifact_ref, validation_evidence=[captured],
                        state="awaiting_verification", supersedes_id=older[-1].id if older else None,
                    )
                    records["deliverables"][item.id] = item.to_dict()

            if status != "succeeded":
                persist_artifacts(failed_run=True)
                run.status = "failed"; run.completed_at = now(); run.error = {"code": "provider_failed", "message": "Codex execution failed."}
                run.lease_owner = run.lease_until = None; self._touch(run); self._event(records, run, "agent_failed", {**run.error, "artifact_candidates": len(artifacts)})
                records["runs"][run.id] = run.to_dict(); return run
            run.usage = _safe_value(result.get("usage", {})); run.completed_agent_ids.append(agent_id)
            run.next_agent_position += 1; run.current_agent_id = None; run.invocation_started_at = None; run.invocation_id = None; run.execution_binding = None
            run.lease_owner = run.lease_until = None
            persist_artifacts(failed_run=False)
            agents = [row for _, row in sorted(records["agents"].items(), key=lambda item: (item[1].get("created_at", ""), item[0]))]
            if run.next_agent_position >= len(agents):
                run.status = "succeeded"; run.completed_at = now(); run.result = {"status": "succeeded"}
            else: run.status = "queued"
            run.progress = {"completed": len(run.completed_agent_ids), "total": len(agents)}
            self._touch(run); self._event(records, run, "agent_completed", {
                "agent_id": agent_id, "response": result.get("response"),
                "structured_output": result.get("structured_output", {}), "events": result.get("events", []),
                "usage": result.get("usage", {}), "session_id": result.get("session_id"),
                "model_id": result.get("model_id"), "artifacts": artifacts,
            })
            records["runs"][run.id] = run.to_dict(); return run
        return self.repository.mutate(tenant_id, project_id, mutate)

    def checkpoint_decision(self, tenant_id: str, project_id: str, approval_id: str, actor_id: str, decision: str, expected_revision: int, expected_run_revision: int) -> MissionRun:
        def mutate(records: dict[str, Any]) -> MissionRun:
            approval = records["approvals"].get(approval_id)
            if not approval: raise MissionNotFoundError("approval not found")
            run = MissionRun.from_dict(records["runs"][approval["run_id"]])
            if run.status != "awaiting_approval" or run.active_approval_id != approval_id or approval["status"] != "pending" or approval["revision"] != expected_revision or run.revision != expected_run_revision: raise MissionConflictError("stale approval or run")
            if actor_id == approval.get("agent_id"): raise PermissionError("producing Mission agent cannot approve its checkpoint")
            approval["status"] = "approved" if decision == "approve" else "rejected"; approval["actor_id"] = actor_id; approval["revision"] += 1; approval["updated_at"] = now()
            if decision == "approve": run.status = "queued"; run.error = None
            else:
                run.status = "cancelled"; run.completed_at = now(); run.error = {"code": "checkpoint_rejected", "message": "Checkpoint was rejected."}; run.active_approval_id = None
            self._touch(run); self._event(records, run, "checkpoint_" + approval["status"], {"approval_id": approval_id})
            records["runs"][run.id] = run.to_dict(); return run
        return self.repository.mutate(tenant_id, project_id, mutate)

    def retry_run(self, tenant_id: str, project_id: str, run_id: str, expected_revision: int, verified_contract_revision: str) -> MissionRun:
        def mutate(records: dict[str, Any]) -> MissionRun:
            run = MissionRun.from_dict(records["runs"][run_id]); mission = self._mission(records)
            if run.revision != expected_revision or run.status not in {"failed", "awaiting_approval"}: raise MissionConflictError("run cannot be retried")
            if not verified_contract_revision: raise MissionConflictError("approved operation graph required")
            self._close_pending(records, run, "retry")
            mission.approved_contract_revision = verified_contract_revision; mission.revision += 1; mission.updated_at = now(); records["mission"] = mission.to_dict()
            run.contract_revision = verified_contract_revision; run.status = "queued"; run.error = None; run.lease_owner = run.lease_until = None; self._touch(run); self._event(records, run, "retry_queued", {})
            records["runs"][run.id] = run.to_dict(); return run
        return self.repository.mutate(tenant_id, project_id, mutate)

    def cancel_run(self, tenant_id: str, project_id: str, run_id: str, expected_revision: int) -> MissionRun:
        def mutate(records: dict[str, Any]) -> MissionRun:
            run = MissionRun.from_dict(records["runs"][run_id])
            if run.revision != expected_revision or run.status not in {"queued", "awaiting_approval"} or run.lease_owner:
                raise MissionConflictError("active provider turns cannot be cancelled")
            self._close_pending(records, run, "cancelled")
            run.status = "cancelled"; run.completed_at = now(); self._touch(run); self._event(records, run, "cancelled", {})
            records["runs"][run.id] = run.to_dict(); return run
        return self.repository.mutate(tenant_id, project_id, mutate)

    def _create_run_locked(self, records: dict[str, Any], tenant_id: str, project_id: str, trigger: Mapping[str, Any], profile: str, occurrence_key: str | None, verified_contract_revision: str | None = None) -> MissionRun:
        if occurrence_key:
            existing = next((MissionRun.from_dict(row) for row in records["runs"].values() if row.get("occurrence_key") == occurrence_key), None)
            if existing:
                return existing
        mission = self._mission(records)
        if mission.approved_contract_revision != verified_contract_revision:
            mission.approved_contract_revision = verified_contract_revision
            mission.revision += 1
            mission.updated_at = now()
            records["mission"] = mission.to_dict()
        run = MissionRun(
            id=new_id("run"), tenant_id=tenant_id, project_id=project_id, mission_id=mission.id,
            trigger_snapshot=dict(trigger), contract_revision=mission.approved_contract_revision,
            execution_profile=self._profile(profile), occurrence_key=occurrence_key,
        )
        records["runs"][run.id] = run.to_dict()
        return run

    def create_run(self, tenant_id: str, project_id: str, trigger: Mapping[str, Any], profile: str = "balanced", occurrence_key: str | None = None, verified_contract_revision: str | None = None) -> MissionRun:
        clean_public_mapping(trigger)
        safe_trigger = _safe_value(trigger)
        if not isinstance(safe_trigger, Mapping):
            raise ValueError("trigger must be a safe mapping")
        return self.repository.mutate(
            tenant_id, project_id,
            lambda records: self._create_run_locked(records, tenant_id, project_id, safe_trigger, profile, occurrence_key, verified_contract_revision),
        )

    def triggers(self, tenant_id: str, project_id: str) -> list[AutomationTrigger]:
        return self._records(tenant_id, project_id, "triggers", AutomationTrigger)

    def add_trigger(self, tenant_id: str, project_id: str, data: Mapping[str, Any]) -> AutomationTrigger:
        clean_public_mapping(data)

        def mutate(records: dict[str, Any]) -> AutomationTrigger:
            mission = self._mission(records)
            trigger = AutomationTrigger(
                id=new_id("trigger"), tenant_id=tenant_id, project_id=project_id, mission_id=mission.id,
                type=str(data["type"]), cron=data.get("cron"), condition=data.get("condition"),
                timezone=str(data.get("timezone") or "UTC"), concurrency_policy=str(data.get("concurrency_policy") or "queue"),
                enabled=bool(data.get("enabled", True)),
            )
            records["triggers"][trigger.id] = trigger.to_dict()
            return trigger

        return self.repository.mutate(tenant_id, project_id, mutate)

    @staticmethod
    def _canonical_occurrence(trigger: AutomationTrigger, facts: Mapping[str, Any]) -> str:
        payload = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return f"{trigger.id}:{hash_artifact(payload)}"

    @staticmethod
    def _cap_trigger_occurrences(records: dict[str, Any], trigger: AutomationTrigger) -> None:
        stale = sorted(
            trigger.handled_occurrences.items(),
            key=lambda item: (str(item[1].get("handled_at", "")), item[0]),
        )[:-TRIGGER_OCCURRENCE_RETENTION]
        for key, _ in stale:
            trigger.handled_occurrences.pop(key, None)
        if stale:
            retention = records.setdefault("retention", {})
            retention["dropped_occurrences"] = int(retention.get("dropped_occurrences", 0)) + len(stale)

    @staticmethod
    def _prune_terminal_runs(records: dict[str, Any]) -> None:
        """Bound unreferenced history while retaining every live/evidenced Run."""
        referenced: set[str] = set()
        for approval in records["approvals"].values():
            if isinstance(approval.get("run_id"), str):
                referenced.add(approval["run_id"])
        for event in records["events"].values():
            if isinstance(event.get("run_id"), str):
                referenced.add(event["run_id"])
        for trigger in records["triggers"].values():
            for handled in trigger.get("handled_occurrences", {}).values():
                if isinstance(handled.get("run_id"), str):
                    referenced.add(handled["run_id"])
        for deliverable in records["deliverables"].values():
            for evidence in deliverable.get("validation_evidence", []):
                if isinstance(evidence, Mapping) and isinstance(evidence.get("run_id"), str):
                    referenced.add(evidence["run_id"])
        terminal = sorted(
            (
                (run_id, raw) for run_id, raw in records["runs"].items()
                if raw.get("status") in {"succeeded", "failed", "cancelled", "expired"}
                and run_id not in referenced
            ),
            key=lambda item: (str(item[1].get("completed_at") or item[1].get("updated_at") or ""), item[0]),
        )
        stale = terminal[:-RUN_HISTORY_RETENTION]
        for run_id, _ in stale:
            records["runs"].pop(run_id, None)
        if stale:
            retention = records.setdefault("retention", {})
            retention["dropped_runs"] = int(retention.get("dropped_runs", 0)) + len(stale)

    @staticmethod
    def _trigger_time(trigger: AutomationTrigger, at: datetime | None) -> datetime:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(trigger.timezone)
        if at is None:
            return datetime.now(zone)
        if at.tzinfo is None:
            return at.replace(tzinfo=zone)
        return at.astimezone(zone)

    def evaluate_due(
        self,
        tenant_id: str,
        project_id: str,
        facts: Mapping[str, Any] | None = None,
        at: datetime | None = None,
        verified_contract_revision: str | None = None,
        *,
        trigger_types: frozenset[str] | None = None,
        require_verified_contract: bool = False,
        active_run_limit: int | None = None,
    ) -> list[MissionRun]:
        facts = facts or {}
        clean_public_mapping(facts)

        def mutate(records: dict[str, Any]) -> list[MissionRun]:
            mission = self._mission(records)
            # Automatic scheduling must present a non-empty revision that its
            # caller has verified against the graph store. The worker pins that
            # exact approved head through this mutation; _create_run_locked then
            # records it on the Mission and Run as one atomic state change.
            if require_verified_contract and not verified_contract_revision:
                return []
            outcome: list[MissionRun] = []
            for raw in records["triggers"].values():
                trigger = AutomationTrigger.from_dict(raw)
                occurrence_count = len(trigger.handled_occurrences)
                self._cap_trigger_occurrences(records, trigger)
                if len(trigger.handled_occurrences) != occurrence_count:
                    trigger.revision += 1
                    trigger.updated_at = now()
                    records["triggers"][trigger.id] = trigger.to_dict()
                if trigger_types is not None and trigger.type not in trigger_types:
                    continue
                due_at = self._trigger_time(trigger, at)
                is_due = trigger.enabled and (
                    # Empty fact sets are not events. In particular, `ne` must
                    # not turn a scheduler poll into a synthetic condition hit.
                    trigger.type == "condition" and bool(facts)
                    and str((trigger.condition or {}).get("fact", "")) in facts
                    and condition_matches(trigger.condition or {}, facts)
                    or trigger.type == "cron" and trigger.next_due_at is not None and datetime.fromisoformat(trigger.next_due_at) <= due_at
                )
                if not is_due:
                    continue
                occurrence = trigger.next_due_at if trigger.type == "cron" else self._canonical_occurrence(trigger, facts)
                key = f"{trigger.id}:{occurrence}"
                handled = trigger.handled_occurrences.get(key)
                if handled is not None:
                    run_id = handled.get("run_id")
                    if run_id and run_id in records["runs"]:
                        outcome.append(MissionRun.from_dict(records["runs"][run_id]))
                    continue
                existing = next(
                    (MissionRun.from_dict(row) for row in records["runs"].values() if row.get("occurrence_key") == key),
                    None,
                )
                if existing is not None:
                    outcome.append(existing)
                    continue
                active = [MissionRun.from_dict(row) for row in records["runs"].values() if row["status"] in ACTIVE_RUN_STATUSES]
                owned = [run for run in active if run.mission_id == trigger.mission_id]
                live = [run for run in owned if run.status == "running" or run.lease_owner or run.invocation_started_at]
                would_create = not (
                    trigger.concurrency_policy == "skip" and owned
                    or trigger.concurrency_policy == "merge" and owned and not live
                )
                if would_create and active_run_limit is not None and len(active) >= active_run_limit:
                    # Automatic scheduling applies durable backpressure. Do not
                    # consume or advance this occurrence until queue capacity exists.
                    continue
                if trigger.concurrency_policy == "skip" and owned:
                    trigger.handled_occurrences[key] = {"outcome": "skipped", "handled_at": now()}
                    self._cap_trigger_occurrences(records, trigger)
                    if trigger.type == "cron":
                        trigger.next_due_at = next_cron_due(trigger.cron or "", trigger.timezone, due_at).isoformat()
                        trigger.revision += 1
                        trigger.updated_at = now()
                        records["triggers"][trigger.id] = trigger.to_dict()
                    continue
                if trigger.concurrency_policy == "replace":
                    if live:
                        # A launched provider may still write.  State-cancelling
                        # it would create two overlapping side-effecting turns.
                        # Preserve this occurrence as one queued successor so
                        # its facts are not silently discarded; claim_next
                        # enforces that it cannot begin until the live run
                        # releases its lease.
                        replacement_outcome = "queued_after_live"
                    else:
                        for run in owned:
                            self._close_pending(records, run, "replaced")
                            run.status = "cancelled"
                            run.completed_at = now()
                            run.revision += 1
                            run.updated_at = now()
                            records["runs"][run.id] = run.to_dict()
                        replacement_outcome = "replaced"
                else:
                    replacement_outcome = "created"
                if trigger.concurrency_policy == "merge" and owned and not live:
                    run = owned[0]
                    occurrences = run.trigger_snapshot.setdefault("merged_occurrences", [])
                    if key not in occurrences:
                        occurrences.append(key)
                        run.revision += 1
                        run.updated_at = now()
                        records["runs"][run.id] = run.to_dict()
                    outcome.append(run)
                    trigger.handled_occurrences[key] = {"outcome": "merged", "run_id": run.id, "handled_at": now()}
                else:
                    created = self._create_run_locked(records, tenant_id, project_id, {"type": trigger.type, "trigger_id": trigger.id, "facts": facts}, "balanced", key, verified_contract_revision)
                    outcome.append(created)
                    trigger.handled_occurrences[key] = {"outcome": "deferred_live_run" if trigger.concurrency_policy == "merge" and live else replacement_outcome, "run_id": created.id, "handled_at": now()}
                self._cap_trigger_occurrences(records, trigger)
                if trigger.type == "cron":
                    trigger.next_due_at = next_cron_due(trigger.cron or "", trigger.timezone, due_at).isoformat()
                    trigger.revision += 1
                    trigger.updated_at = now()
                    records["triggers"][trigger.id] = trigger.to_dict()
                else:
                    trigger.revision += 1
                    trigger.updated_at = now()
                    records["triggers"][trigger.id] = trigger.to_dict()
            self._prune_terminal_runs(records)
            return outcome

        return self.repository.mutate(tenant_id, project_id, mutate)

    def evaluate_cron_due(
        self,
        tenant_id: str,
        project_id: str,
        *,
        at: datetime | None = None,
        verified_contract_revision: str | None,
    ) -> list[MissionRun]:
        """Evaluate only durable cron occurrences for the automatic worker.

        Condition triggers intentionally remain fact/event-driven through
        :meth:`evaluate_due`; an empty scheduler fact set can never fire them.
        """
        return self.evaluate_due(
            tenant_id,
            project_id,
            {},
            at,
            verified_contract_revision,
            trigger_types=frozenset({"cron"}),
            require_verified_contract=True,
            active_run_limit=AUTOMATIC_ACTIVE_RUN_LIMIT,
        )

    def evaluate_condition_due(
        self,
        tenant_id: str,
        project_id: str,
        facts: Mapping[str, Any],
        *,
        at: datetime | None = None,
        verified_contract_revision: str | None,
    ) -> list[MissionRun]:
        """Evaluate only fact/event conditions against a verified graph head."""
        return self.evaluate_due(
            tenant_id,
            project_id,
            facts,
            at,
            verified_contract_revision,
            trigger_types=frozenset({"condition"}),
            require_verified_contract=True,
        )

    def deliverables(self, tenant_id: str, project_id: str) -> list[Deliverable]:
        return self._records(tenant_id, project_id, "deliverables", Deliverable)

    def create_deliverable(self, tenant_id: str, project_id: str, data: Mapping[str, Any], producer_id: str, artifact_bytes: bytes) -> Deliverable:
        clean_public_mapping(data)
        if "state" in data:
            raise ValueError("new deliverables always await verification")
        if "artifact_content" in data:
            raise ValueError("artifact bytes must be read by the server from artifact_ref")

        def mutate(records: dict[str, Any]) -> Deliverable:
            mission = self._mission(records)
            requested_agent = data.get("producer_agent_id")
            if requested_agent is not None:
                agent = records["agents"].get(requested_agent)
                if agent is None or agent.get("mission_id") != mission.id:
                    raise ValueError("producer_agent_id must identify a Mission agent")
                producer = requested_agent
            else:
                producer = producer_id
            older = [Deliverable.from_dict(row) for row in records["deliverables"].values() if row["name"] == data["name"]]
            older.sort(key=lambda value: value.version)
            source = str(data["source_ref"])
            item = Deliverable(
                id=new_id("deliverable"), tenant_id=tenant_id, project_id=project_id, mission_id=mission.id,
                type=str(data["type"]), name=str(data["name"]), producer_id=producer,
                version=max((value.version for value in older), default=0) + 1,
                content_hash=hash_artifact(artifact_bytes), source_ref=source,
                artifact_ref=data.get("artifact_ref"), validation_evidence=list(data.get("validation_evidence") or []),
                state="awaiting_verification", supersedes_id=older[-1].id if older else None,
            )
            records["deliverables"][item.id] = item.to_dict()
            return item

        return self.repository.mutate(tenant_id, project_id, mutate)

    def verify_deliverable(self, tenant_id: str, project_id: str, deliverable_id: str, actor_id: str, content_hash: str, expected_revision: int,
                           promote: Callable[[Deliverable], None] | None = None) -> Deliverable:
        def mutate(records: dict[str, Any]) -> Deliverable:
            mission = self._mission(records)
            if deliverable_id not in records["deliverables"]:
                raise MissionNotFoundError("deliverable not found")
            item = Deliverable.from_dict(records["deliverables"][deliverable_id])
            if actor_id == item.producer_id:
                raise PermissionError("producer cannot verify a deliverable")
            if actor_id not in mission.verifier_ids and actor_id != mission.owner_id:
                raise PermissionError("designated verifier required")
            if item.revision != expected_revision or item.content_hash != content_hash:
                raise MissionConflictError("stale deliverable revision or hash")
            if promote is not None:
                promote(item)
            item.state = "verified"
            item.verified_by = actor_id
            item.verified_hash = content_hash
            item.verified_at = now()
            item.revision += 1
            item.updated_at = now()
            records["deliverables"][item.id] = item.to_dict()
            return item

        return self.repository.mutate(tenant_id, project_id, mutate)
