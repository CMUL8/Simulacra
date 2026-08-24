"""Mission V0 domain service; all writes use repository transactions."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any, Mapping

from .models import (
    PROFILES, AgentDefinition, AutomationTrigger, Deliverable, Mission,
    MissionRun, clean_public_mapping, condition_matches, hash_artifact,
    new_id, next_cron_due, now,
)
from .repository import JsonMissionRepository, MissionConflictError, MissionNotFoundError

ACTIVE_RUN_STATUSES = {"queued", "preparing", "running", "awaiting_approval", "verifying"}


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
            mission = Mission(
                id=new_id("mission"), tenant_id=tenant_id, project_id=project_id,
                owner_id=owner_id, title=str(data.get("title") or "Mission"),
                objective=str(data.get("objective") or ""),
                definition_of_done=str(data.get("definition_of_done") or ""),
                template=str(data.get("template") or "custom"),
                verifier_ids=list(data.get("verifier_ids") or [owner_id]),
                priority=str(data.get("priority") or "normal"),
                risk_level=str(data.get("risk_level") or "medium"),
                deadline=data.get("deadline"), budget=dict(data.get("budget") or {}),
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
                    setattr(mission, key, patch[key])
            mission.revision += 1
            mission.updated_at = now()
            mission.__post_init__()
            records["mission"] = mission.to_dict()
            return mission

        return self.repository.mutate(tenant_id, project_id, mutate)

    def _records(self, tenant_id: str, project_id: str, name: str, cls: type[Any]) -> list[Any]:
        return [cls.from_dict(value) for _, value in sorted(self.repository.list_collection(tenant_id, project_id, name).items())]

    def agents(self, tenant_id: str, project_id: str) -> list[AgentDefinition]:
        return self._records(tenant_id, project_id, "agents", AgentDefinition)

    def add_agent(self, tenant_id: str, project_id: str, data: Mapping[str, Any]) -> AgentDefinition:
        clean_public_mapping(data)

        def mutate(records: dict[str, Any]) -> AgentDefinition:
            mission = self._mission(records)
            agent = AgentDefinition(
                id=new_id("agent"), tenant_id=tenant_id, project_id=project_id, mission_id=mission.id,
                name=str(data["name"]), role=str(data["role"]), mandate=str(data["mandate"]),
                responsibilities=list(data.get("responsibilities") or []), data_scope=list(data.get("data_scope") or []),
                tools=list(data.get("tools") or []), autonomy=str(data.get("autonomy") or "assist"),
                escalation_actor_id=data.get("escalation_actor_id"), budget=dict(data.get("budget") or {}),
            )
            records["agents"][agent.id] = agent.to_dict()
            return agent

        return self.repository.mutate(tenant_id, project_id, mutate)

    def runs(self, tenant_id: str, project_id: str) -> list[MissionRun]:
        return self._records(tenant_id, project_id, "runs", MissionRun)

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
        return self.repository.mutate(
            tenant_id, project_id,
            lambda records: self._create_run_locked(records, tenant_id, project_id, trigger, profile, occurrence_key, verified_contract_revision),
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
    def _trigger_time(trigger: AutomationTrigger, at: datetime | None) -> datetime:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(trigger.timezone)
        if at is None:
            return datetime.now(zone)
        if at.tzinfo is None:
            return at.replace(tzinfo=zone)
        return at.astimezone(zone)

    def evaluate_due(self, tenant_id: str, project_id: str, facts: Mapping[str, Any] | None = None, at: datetime | None = None, verified_contract_revision: str | None = None) -> list[MissionRun]:
        facts = facts or {}
        clean_public_mapping(facts)

        def mutate(records: dict[str, Any]) -> list[MissionRun]:
            self._mission(records)
            outcome: list[MissionRun] = []
            for raw in records["triggers"].values():
                trigger = AutomationTrigger.from_dict(raw)
                due_at = self._trigger_time(trigger, at)
                is_due = trigger.enabled and (
                    trigger.type == "condition" and condition_matches(trigger.condition or {}, facts)
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
                if trigger.concurrency_policy == "skip" and owned:
                    trigger.handled_occurrences[key] = {"outcome": "skipped"}
                    if trigger.type == "cron":
                        trigger.next_due_at = next_cron_due(trigger.cron or "", trigger.timezone, due_at).isoformat()
                        trigger.revision += 1
                        trigger.updated_at = now()
                        records["triggers"][trigger.id] = trigger.to_dict()
                    continue
                if trigger.concurrency_policy == "replace":
                    for run in owned:
                        run.status = "cancelled"
                        run.completed_at = now()
                        run.revision += 1
                        run.updated_at = now()
                        records["runs"][run.id] = run.to_dict()
                    replacement_outcome = "replaced"
                else:
                    replacement_outcome = "created"
                if trigger.concurrency_policy == "merge" and owned:
                    run = owned[0]
                    occurrences = run.trigger_snapshot.setdefault("merged_occurrences", [])
                    if key not in occurrences:
                        occurrences.append(key)
                        run.revision += 1
                        run.updated_at = now()
                        records["runs"][run.id] = run.to_dict()
                    outcome.append(run)
                    trigger.handled_occurrences[key] = {"outcome": "merged", "run_id": run.id}
                else:
                    created = self._create_run_locked(records, tenant_id, project_id, {"type": trigger.type, "trigger_id": trigger.id, "facts": facts}, "balanced", key, verified_contract_revision)
                    outcome.append(created)
                    trigger.handled_occurrences[key] = {"outcome": replacement_outcome, "run_id": created.id}
                if trigger.type == "cron":
                    trigger.next_due_at = next_cron_due(trigger.cron or "", trigger.timezone, due_at).isoformat()
                    trigger.revision += 1
                    trigger.updated_at = now()
                    records["triggers"][trigger.id] = trigger.to_dict()
                else:
                    trigger.revision += 1
                    trigger.updated_at = now()
                    records["triggers"][trigger.id] = trigger.to_dict()
            return outcome

        return self.repository.mutate(tenant_id, project_id, mutate)

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

    def verify_deliverable(self, tenant_id: str, project_id: str, deliverable_id: str, actor_id: str, content_hash: str, expected_revision: int) -> Deliverable:
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
            item.state = "verified"
            item.verified_by = actor_id
            item.verified_hash = content_hash
            item.verified_at = now()
            item.revision += 1
            item.updated_at = now()
            records["deliverables"][item.id] = item.to_dict()
            return item

        return self.repository.mutate(tenant_id, project_id, mutate)
