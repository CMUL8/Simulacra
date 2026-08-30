"""One-at-a-time durable Mission consumer with a certified executor boundary."""
from __future__ import annotations

import asyncio
import os
import socket
import json
import stat
import hashlib
import tempfile
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from simulacra.harnesses import (
    AgentRunRequest, HarnessConfig, JsonSessionRepository,
    MissionIsolationSpec, NetworkPolicy, TaskType, create_harness,
)
from simulacra.harnesses.codex import CodexAppServerTransport
from simulacra.harnesses.provider_route import ResponsesProviderRoute
from simulacra.operation_graph import OperationGraphStore

from .artifacts import artifact_evidence
from .executor import MissionAgentExecutor
from .models import AgentDefinition, MissionRun, effective_budget
from .repository import MissionConflictError
from .service import MissionService

_TOOLS = frozenset({"document.read", "code.read", "artifact.write", "code.write"})
_BAKED_LAUNCHER = Path("/opt/cmul8/bin/cmul8-mission-sandbox")
_PRODUCTION_LAUNCHER = Path("/opt/cmul8/bin/cmul8-mission-sandbox")
_CODEX_RUNTIME_ROOT = Path("/opt/codex")
_DEFAULT_MISSION_RUNTIME_ROOT = Path("/app/data/mission-runtime")


def _trusted_launcher(path: Path, configured: str) -> bool:
    """Production trust check; tests replace this narrow predicate explicitly."""
    try:
        info = path.lstat()
    except OSError:
        return False
    return (configured == str(_BAKED_LAUNCHER) and path == _BAKED_LAUNCHER and not path.is_symlink()
            and stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o555)


@dataclass(slots=True)
class _IsolationResources:
    spec: MissionIsolationSpec
    executable: str
    manifest: Path
    manifest_inode: tuple[int, int]
    temp_root: Path
    temp_inode: tuple[int, int]

    def cleanup(self) -> None:
        """Remove only the files/directories created for this invocation."""
        try:
            info = os.lstat(self.manifest)
            if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == self.manifest_inode:
                self.manifest.unlink()
        except FileNotFoundError:
            pass
        try:
            info = os.lstat(self.temp_root)
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode) and (info.st_dev, info.st_ino) == self.temp_inode:
                shutil.rmtree(self.temp_root, ignore_errors=True)
        except FileNotFoundError:
            pass


class CodexMissionAgentExecutor(MissionAgentExecutor):
    """Built-in executor backed by the open Codex app-server harness."""

    name = "codex"
    protocol = "codex-app-server-v1"
    enforces_network_policy = True

    def runtime_root(self) -> Path:
        return _CODEX_RUNTIME_ROOT

    def executable_path(self) -> Path:
        return Path(os.environ.get("CMUL8_CODEX_BIN", str(_CODEX_RUNTIME_ROOT / "bin" / "codex")))

    def execute(self, request: AgentRunRequest, *, isolation: _IsolationResources,
                session_repository: JsonSessionRepository):
        harness = create_harness(
            request.config,
            session_repository=session_repository,
            codex_transport=CodexAppServerTransport(
                executable=isolation.executable,
                isolation_spec=isolation.spec,
            ),
        )
        return asyncio.run(harness.run(request))


class _HarnessFactoryMissionAgentExecutor(MissionAgentExecutor):
    """Compatibility seam for deterministic in-process tests and migrations."""

    def __init__(self, factory: Callable[..., Any]) -> None:
        self.factory = factory
        self.name = ""

    def config_for(self, run: MissionRun) -> HarnessConfig:
        base = HarnessConfig.from_env()
        self.name = str(run.execution_profile.get("runtime") or base.harness)
        return super().config_for(run)

    def readiness_error(self, config: HarnessConfig, *, isolation_ready: bool) -> tuple[str, str] | None:
        return None

    def execute(self, request: AgentRunRequest, *, isolation: _IsolationResources | None,
                session_repository: JsonSessionRepository):
        harness = self.factory(request.config, session_repository=session_repository)
        return asyncio.run(harness.run(request))


class MissionWorker:
    def __init__(self, service: MissionService, workspace: str | Path, worker_id: str | None = None,
                 harness_factory: Callable[..., Any] = create_harness, coordinator: Any | None = None,
                 execution_backend: MissionAgentExecutor | None = None) -> None:
        self.service, self.workspace = service, Path(workspace).resolve(),
        self.worker_id = worker_id or os.environ.get("CMUL8_WORKER_ID", f"mission-{socket.gethostname()}")
        self.harness_factory = harness_factory
        self.coordinator = coordinator
        if execution_backend is not None and harness_factory is not create_harness:
            raise ValueError("supply either execution_backend or the legacy harness_factory, not both")
        self.execution_backend = execution_backend or (
            CodexMissionAgentExecutor() if harness_factory is create_harness
            else _HarnessFactoryMissionAgentExecutor(harness_factory)
        )

    def _admitted(self, run: MissionRun):
        try:
            mission = self.service.mission(run.tenant_id, run.project_id)
            store = OperationGraphStore(self.workspace, tenant_id=run.tenant_id, project_id=run.project_id)
            current = store.current_revision()
            if not run.contract_revision or not current:
                return False, "operation_graph_required", None
            if run.contract_revision != mission.approved_contract_revision or run.contract_revision != current.revision_hash:
                return False, "contract_changed", None
            store.require_approved_revision(run.contract_revision)
            return True, "", current
        except Exception:
            return False, "operation_graph_required", None

    def schedule_due_cron(self, tenant_id: str, project_id: str) -> list[MissionRun]:
        """Atomically enqueue due cron occurrences against the exact approved graph.

        The graph store and Mission store are separate durable boundaries. We
        prove that the workspace's current revision is itself approved and pin
        that head until the Mission transaction records the same hash on both
        the Mission and Run. A missing approval therefore leaves the occurrence
        unhandled for a later scheduler tick.
        """
        try:
            store = OperationGraphStore(
                self.workspace, tenant_id=tenant_id, project_id=project_id,
            )
            # Keep the graph head pinned until the Mission repository commits
            # its occurrence/run mutation. Graph creation and rollback share
            # this lock, closing the otherwise unavoidable cross-store race.
            with store.locked_current_approved_revision() as current:
                if current is None:
                    return []
                return self.service.evaluate_cron_due(
                    tenant_id,
                    project_id,
                    verified_contract_revision=current.revision_hash,
                )
        except Exception:
            return []

    def _paths(self, agent: AgentDefinition, run: MissionRun) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
        if any(not isinstance(item, str) or item not in _TOOLS for item in agent.tools):
            raise ValueError("Mission agent requested an unsupported tool")
        reads: list[Path] = []
        for raw in agent.data_scope:
            value = Path(raw)
            if value.is_absolute() or "\\" in raw or (len(raw) > 1 and raw[1] == ":") or not value.parts or any(part in {"", ".", "..", ".codex", ".cmul8", ".mission-control", "audit", "control"} or any(ord(c) < 32 for c in part) for part in value.parts):
                raise ValueError("Mission data scope must be a safe relative path")
            resolved = (self.workspace / value).resolve(strict=False)
            if self.workspace not in resolved.parents and resolved != self.workspace:
                raise ValueError("Mission data scope escapes workspace")
            probe = self.workspace
            for part in value.parts:
                probe = probe / part
                if probe.is_symlink(): raise ValueError("Mission data scope may not contain symlinks")
            if resolved.exists(): reads.append(resolved)
        writes: list[Path] = []
        if agent.autonomy != "assist" and "artifact.write" in agent.tools:
            writes.append(self._secure_output_directory(run.id, agent.id))
        if agent.autonomy != "assist" and "code.write" in agent.tools:
            app = self.workspace / "app"
            # Canonical app files are public-preview material. Codex may read
            # an existing verified app, but can only create/replace candidates
            # in a private, per-run staging directory.
            if app.exists():
                if not app.is_dir() or app.is_symlink(): raise ValueError("approved app directory is unavailable")
                reads.append(app.resolve())
            writes.append(self._secure_code_staging_directory(run.id, agent.id, run.revision))
        return tuple(reads), tuple(writes)

    def _secure_code_staging_directory(self, run_id: str, agent_id: str, attempt_revision: int) -> Path:
        root = self._secure_output_directory(run_id, agent_id)
        if not isinstance(attempt_revision, int) or attempt_revision < 1:
            raise ValueError("invalid Mission code staging attempt")
        stage_name = f"code-staging-r{attempt_revision}"
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(root, flags)
        try:
            try:
                os.mkdir(stage_name, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(stage_name, flags, dir_fd=descriptor)
            try:
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode) or os.listdir(child):
                    raise ValueError("Mission code staging directory must start empty")
            finally:
                os.close(child)
        except OSError as exc:
            raise ValueError("unsafe Mission code staging directory") from exc
        finally:
            os.close(descriptor)
        return root / stage_name

    def _secure_output_directory(self, run_id: str, agent_id: str) -> Path:
        """Create the only mutable artifact directory through no-follow FDs."""
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = os.open(self.workspace, flags)
        descriptors = [descriptor]
        parts = ("outputs", "missions", run_id, agent_id)
        try:
            for part in parts:
                try:
                    os.mkdir(part, 0o700, dir_fd=descriptors[-1])
                except FileExistsError:
                    pass
                child = os.open(part, flags, dir_fd=descriptors[-1])
                info = os.fstat(child)
                if not stat.S_ISDIR(info.st_mode):
                    os.close(child); raise ValueError("unsafe Mission output directory")
                descriptors.append(child)
            return self.workspace.joinpath(*parts)
        except OSError as exc:
            raise ValueError("unsafe Mission output directory") from exc
        finally:
            for item in reversed(descriptors): os.close(item)

    def _config(self, run: MissionRun) -> HarnessConfig:
        return self.execution_backend.config_for(run)

    @staticmethod
    def _private_directory(path: Path, *, root: Path) -> None:
        """Create a worker-owned directory without traversing a symlink."""
        root = root.resolve(strict=True)
        try:
            relative = path.relative_to(root)
        except ValueError as exc:
            raise ValueError("private directory escapes worker root") from exc
        probe = root
        for part in relative.parts:
            probe = probe / part
            try:
                probe.mkdir(mode=0o700)
            except FileExistsError:
                pass
            info = os.lstat(probe)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError("unsafe worker control directory")
            os.chmod(probe, 0o700)

    @staticmethod
    def _runtime_root() -> Path:
        configured = Path(os.environ.get("CMUL8_MISSION_RUNTIME_ROOT", str(_DEFAULT_MISSION_RUNTIME_ROOT)))
        if not configured.is_absolute() or configured.is_symlink():
            raise ValueError("Mission runtime root must be an absolute non-symlink path")
        parent = configured.parent.resolve(strict=True)
        if configured.parent != parent:
            raise ValueError("Mission runtime root may not have symlink ancestors")
        if configured == Path("/app/runs") or Path("/app/runs") in configured.parents:
            raise ValueError("Mission runtime root may not be under /app/runs")
        try:
            configured.mkdir(mode=0o700)
        except FileExistsError:
            pass
        info = os.lstat(configured)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("unsafe Mission runtime root")
        os.chmod(configured, 0o700)
        return configured.resolve(strict=True)

    def _sandbox_ready(self) -> bool:
        launcher = os.environ.get("CMUL8_MISSION_ISOLATION_LAUNCHER", "")
        try:
            return _trusted_launcher(Path(launcher), launcher)
        except (OSError, ValueError):
            return False

    def _isolation(self, run: MissionRun, agent: AgentDefinition, reads: tuple[Path, ...], writes: tuple[Path, ...],
                   config: HarnessConfig | None = None) -> _IsolationResources | None:
        """Make a one-shot, descriptor-safe manifest for the trusted launcher."""
        launcher = os.environ.get("CMUL8_MISSION_ISOLATION_LAUNCHER", "")
        try:
            path = Path(launcher)
            if not _trusted_launcher(path, launcher):
                return None
            expected_runtime = Path(self.execution_backend.runtime_root())
            expected_executable = Path(self.execution_backend.executable_path())
            if not expected_runtime.is_absolute() or not expected_executable.is_absolute():
                return None
            runtime = expected_runtime.resolve(strict=True)
            executable = expected_executable.resolve(strict=True)
            runtime_info, executable_info = os.lstat(runtime), os.lstat(executable)
            expected_owner = 0 if path == _PRODUCTION_LAUNCHER and launcher == str(_PRODUCTION_LAUNCHER) else os.getuid()
            if (
                runtime != expected_runtime or runtime.is_symlink()
                or not stat.S_ISDIR(runtime_info.st_mode)
                or executable.is_symlink() or not stat.S_ISREG(executable_info.st_mode)
                or runtime not in executable.parents
                or runtime_info.st_uid != expected_owner
                or executable_info.st_uid != expected_owner
                or runtime_info.st_mode & 0o022
                or executable_info.st_mode & 0o022
            ):
                return None
            runtime_root = self._runtime_root()
            if runtime_root == self.workspace or runtime_root in self.workspace.parents or self.workspace in runtime_root.parents:
                return None
            # The home is durable only for one Mission agent.  That preserves
            # the official Codex thread id across long-haul runs/retries, while
            # keeping every other mission or agent from reusing its state. Each
            # new app-server still re-pins trust/provider and clears skills/MCP.
            state_root = runtime_root / self.execution_backend.state_namespace / run.tenant_id / run.project_id / run.mission_id / agent.id
            self._private_directory(state_root, root=runtime_root)
            temp_root = Path(tempfile.mkdtemp(prefix="mission-turn-", dir=runtime_root))
            os.chmod(temp_root, 0o700)
            temp_info = os.lstat(temp_root)
            if not stat.S_ISDIR(temp_info.st_mode) or stat.S_ISLNK(temp_info.st_mode):
                raise ValueError("unsafe Mission temporary root")
            descriptor, manifest_name = tempfile.mkstemp(prefix="mission-manifest-", suffix=".json", dir=runtime_root)
            manifest = Path(manifest_name)
            created_manifest_info = os.fstat(descriptor)
            manifest_inode = (created_manifest_info.st_dev, created_manifest_info.st_ino)
            try:
                os.fchmod(descriptor, 0o600)
                selected_config = config or self._config(run)
                route = ResponsesProviderRoute.from_config(selected_config)
                payload = {
                    "workspace": str(self.workspace.resolve(strict=True)),
                    "read_roots": sorted(str(item.resolve(strict=True)) for item in reads),
                    "write_roots": sorted(str(item.resolve(strict=True)) for item in writes),
                    "temp_root": str(temp_root.resolve(strict=True)),
                    "executor_home": str(state_root.resolve(strict=True)),
                    "executor_runtime_root": str(runtime),
                    "executor_executable": str(executable),
                    "execution_backend": self.execution_backend.name,
                    "execution_protocol": self.execution_backend.protocol,
                    "network": False,
                    "mission_id": run.mission_id,
                    "run_id": run.id,
                    "agent_id": agent.id,
                    "model_route": route.to_manifest(),
                }
                if self.execution_backend.name == "codex":
                    # One-release compatibility for the pinned Codex launcher
                    # and operational manifest inspection.
                    payload.update({
                        "codex_home": payload["executor_home"],
                        "codex_runtime_root": payload["executor_runtime_root"],
                        "codex_executable": payload["executor_executable"],
                    })
                if run.invocation_id and run.execution_binding:
                    payload["invocation_id"] = run.invocation_id
                    payload["execution_binding_sha256"] = self.service._binding_digest(run.execution_binding)
                encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            manifest_info = os.lstat(manifest)
            if not stat.S_ISREG(manifest_info.st_mode) or stat.S_ISLNK(manifest_info.st_mode) or stat.S_IMODE(manifest_info.st_mode) != 0o600:
                raise ValueError("unsafe Mission manifest")
            spec = MissionIsolationSpec.from_files(launcher=path, manifest=manifest)
            return _IsolationResources(spec, str(executable), manifest, manifest_inode, temp_root, (temp_info.st_dev, temp_info.st_ino))
        except (OSError, ValueError, RuntimeError):
            # If construction fails after mkdtemp/mkstemp the caller has no
            # resource object yet, so remove only paths below our private root.
            try:
                if "manifest" in locals() and "manifest_inode" in locals():
                    current = os.lstat(manifest)
                    if stat.S_ISREG(current.st_mode) and (current.st_dev, current.st_ino) == manifest_inode:
                        manifest.unlink()
                if "temp_root" in locals() and "temp_info" in locals():
                    current = os.lstat(temp_root)
                    if stat.S_ISDIR(current.st_mode) and not stat.S_ISLNK(current.st_mode) and (current.st_dev, current.st_ino) == (temp_info.st_dev, temp_info.st_ino):
                        shutil.rmtree(temp_root, ignore_errors=True)
            except OSError:
                pass
            return None

    def _prompt(self, run: MissionRun, agent: AgentDefinition, reads: tuple[Path, ...], writes: tuple[Path, ...], budget: Mapping[str, int]) -> str:
        mission = self.service.mission(run.tenant_id, run.project_id)
        handoffs = self.service.run_handoffs(run.tenant_id, run.project_id, run.id)
        handoff_text = json.dumps(handoffs, ensure_ascii=False, sort_keys=True)[:12000] if handoffs else "No previous crew output. You are the first assigned agent."
        return "\n".join((
            f"Mission title: {mission.title}", f"Objective: {mission.objective}", f"Definition of done: {mission.definition_of_done}",
            f"Run id: {run.id}", f"Trigger: {run.trigger_snapshot}", f"Agent: {agent.name} ({agent.role})", f"Mandate: {agent.mandate}",
            f"Responsibilities: {', '.join(agent.responsibilities)}", f"Read scopes: {', '.join(str(path.relative_to(self.workspace)) for path in reads) or 'none'}",
            f"Write scopes: {', '.join(str(path.relative_to(self.workspace)) for path in writes) or 'none'}", "Network access is denied.",
            f"Execution budget: at most {budget['max_steps']} tool actions and {budget['wall_timeout_seconds']} seconds wall time.",
            "Previous crew handoffs (use them, verify them against sources, and move the Mission forward):", handoff_text,
            "Only produce output inside the listed write scopes; report changed artifact paths exactly.",
        ))

    def run_once(self, tenant_id: str, project_id: str) -> MissionRun | None:
        return self.consume(tenant_id, project_id)

    def consume(self, tenant_id: str, project_id: str) -> MissionRun | None:
        if self.coordinator is None:
            run = self.service.claim_next(tenant_id, project_id, self.worker_id)
        else:
            self.coordinator.recover_project(tenant_id, project_id)
            # A committed agent result must reach the humans' shared room
            # before more work is claimed. Re-running this repair is safe and
            # closes a crash between Mission completion and Conversation write.
            try:
                self.coordinator.project_agent_results(tenant_id, project_id)
            except Exception:
                return None
            with self.coordinator.project_claim_guard(tenant_id, project_id) as admission:
                run = self.service.claim_next(tenant_id, project_id, self.worker_id, assignment_admission=admission)
        if run is None: return None
        admitted, code, graph = self._admitted(run)
        if not admitted:
            return self.service.gate(tenant_id, project_id, run.id, code, "An exact approved Operation Graph is required before the agent can run.", lease_owner=self.worker_id)
        try:
            agents = self.service.run_agents(tenant_id, project_id, run)
        except MissionConflictError:
            return self.service.gate(tenant_id, project_id, run.id, "crew_changed", "An assigned Mission agent is no longer available.", lease_owner=self.worker_id)
        if not agents:
            return self.service.gate(tenant_id, project_id, run.id, "crew_required", "Add a Mission agent before execution.", lease_owner=self.worker_id)
        if run.next_agent_position >= len(agents):
            return self.service.finalize_recovered_run(tenant_id, project_id, run.id, self.worker_id)
        agent = agents[run.next_agent_position]
        if agent.autonomy == "operate_with_checkpoints":
            # Execution must consult the exact active approval, not the
            # bounded Work/overview history where old actionable records could
            # otherwise be omitted by recent closed activity.
            approval = self.service.approval(tenant_id, project_id, run.active_approval_id) if run.active_approval_id else None
            approved = bool(approval and approval.get("id") == run.active_approval_id and approval.get("agent_id") == agent.id and approval.get("status") == "approved")
            if not approved:
                return self.service.gate(tenant_id, project_id, run.id, "checkpoint_required", "Human approval is required before this agent can run.", lease_owner=self.worker_id, agent_id=agent.id)
        try:
            reads, writes = self._paths(agent, run)
        except ValueError:
            return self.service.gate(tenant_id, project_id, run.id, "capability_required", "Mission agent capabilities are not valid for this workspace.", lease_owner=self.worker_id, agent_id=agent.id)
        # Validate exact contract again immediately before the durable start marker.
        admitted, code, graph = self._admitted(run)
        if not admitted:
            return self.service.gate(tenant_id, project_id, run.id, code, "The approved Operation Graph changed before the agent could start.", lease_owner=self.worker_id)
        try:
            config = self._config(run)
            route_binding = dict(self.execution_backend.route_binding(config))
        except ValueError:
            return self.service.gate(tenant_id, project_id, run.id, "model_route_invalid", "The managed model route is not valid.", lease_owner=self.worker_id, agent_id=agent.id)
        compatibility_test_backend = isinstance(self.execution_backend, _HarnessFactoryMissionAgentExecutor)
        readiness_error = self.execution_backend.readiness_error(
            config,
            isolation_ready=compatibility_test_backend or self._sandbox_ready(),
        )
        if readiness_error is not None:
            error_code, error_message = readiness_error
            return self.service.gate(tenant_id, project_id, run.id, error_code, error_message, lease_owner=self.worker_id, agent_id=agent.id)
        started: MissionRun | None = None
        try:
            budget = effective_budget(self.service.mission(tenant_id, project_id).budget, agent.budget)
            prompt = self._prompt(run, agent, reads, writes, budget)
            if graph is None:
                return self.service.gate(tenant_id, project_id, run.id, "operation_graph_required", "The approved Operation Graph is unavailable.", lease_owner=self.worker_id)
            binding = {
                "operation_graph_revision": graph.revision,
                "operation_graph_hash": graph.revision_hash,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "role": f"mission:{run.mission_id}:agent:{agent.id}",
                "tools": list(agent.tools), "autonomy": agent.autonomy,
                "execution_profile": run.execution_profile,
                "runtime_config": config.persisted_identity(),
                "model_route": route_binding,
                "assigned_agent_ids": list(run.assigned_agent_ids),
                "effective_budget": budget,
            }
            started = self.service.mark_agent_started(tenant_id, project_id, run.id, agent.id, self.worker_id, prompt, binding)
            if self.coordinator is not None:
                try:
                    self.coordinator.project_agent_results(tenant_id, project_id)
                except Exception:
                    # Do not begin invisible work. The durable start event and
                    # a stopped run are repaired into public product state on
                    # the next worker tick; the executor is never called.
                    return self.service.record_result(
                        tenant_id, project_id, started.id, self.worker_id, agent.id,
                        {"status": "failed"}, [],
                    )
            role_key = f"mission:{started.mission_id}:agent:{agent.id}"
            # The graph's head and immutable bytes are checked once more at the
            # launch edge. A changed graph is a durable failed turn, never a
            # launch under a stale approval.
            admitted, code, current_graph = self._admitted(started)
            if not admitted or current_graph is None or started.execution_binding is None or current_graph.revision != started.execution_binding.get("operation_graph_revision"):
                return self.service.record_result(tenant_id, project_id, started.id, self.worker_id, agent.id, {"status": "failed"}, [])
            request = AgentRunRequest(project_id=project_id, environment_id="production", workspace=self.workspace,
                prompt=prompt, role=role_key,
                task_type=TaskType.RESEARCH if agent.autonomy == "assist" else TaskType.BUILD_APP,
                read_paths=reads, write_paths=writes, network_policy=NetworkPolicy.DENY,
                wall_timeout_seconds=budget["wall_timeout_seconds"], step_budget=budget["max_steps"],
                config=config, session_id=started.session_ids.get(agent.id), metadata={"mission_id": started.mission_id, "run_id": started.id, "agent_id": agent.id, "invocation_id": started.invocation_id, "execution_binding_sha256": self.service._binding_digest(started.execution_binding)})
            isolation = None if compatibility_test_backend else self._isolation(
                started, agent, reads, writes, request.config,
            )
            if not compatibility_test_backend and isolation is None:
                return self.service.record_result(
                    tenant_id, project_id, started.id, self.worker_id, agent.id,
                    {"status": "failed"}, [],
                )
            try:
                result = self.execution_backend.execute(
                    request,
                    isolation=isolation,
                    session_repository=JsonSessionRepository(self.workspace),
                )
            finally:
                if isolation is not None:
                    isolation.cleanup()
            if (
                result.harness != config.harness
                or result.provider != config.provider.provider
                or result.model_id != config.model.model_id
            ):
                return self.service.record_result(
                    tenant_id, project_id, started.id, self.worker_id, agent.id,
                    {"status": "failed"}, [],
                )
            changed: list[dict[str, object]] = []
            invalid_artifact = False
            code_staging = next((path for path in writes if path.name.startswith("code-staging-r") and path.name[14:].isdigit()), None)
            for changed_path in result.changed_files:
                try:
                    candidate = Path(changed_path)
                    candidate = candidate if candidate.is_absolute() else self.workspace / candidate
                    relative_candidate = candidate.relative_to(self.workspace)
                    probe = self.workspace
                    for part in relative_candidate.parts:
                        probe = probe / part
                        if probe.is_symlink(): raise ValueError("provider artifact may not be a symlink")
                    resolved = candidate.resolve(strict=False)
                    relative = resolved.relative_to(self.workspace).as_posix()
                    if not any(resolved == root or root in resolved.parents for root in writes): raise ValueError("outside write authority")
                    _, evidence = artifact_evidence(self.workspace, relative)
                    if code_staging is not None and (resolved == code_staging or code_staging in resolved.parents):
                        staged_relative = resolved.relative_to(code_staging)
                        if not staged_relative.parts:
                            raise ValueError("staged code artifact must be a file")
                        evidence["staged_artifact_ref"] = relative
                        evidence["intended_target"] = (Path("app") / staged_relative).as_posix()
                        evidence["run_id"] = started.id
                        evidence["agent_id"] = agent.id
                    changed.append(evidence)
                except (ValueError, OSError):
                    invalid_artifact = True
            if invalid_artifact and writes:
                return self.service.record_result(tenant_id, project_id, started.id, self.worker_id, agent.id, {"status": "failed"}, [])
            completed = self.service.record_result(tenant_id, project_id, started.id, self.worker_id, agent.id, {
                "status": result.status.value, "session_id": result.session_id, "response": result.response,
                "structured_output": dict(result.structured_output), "usage": dict(result.usage),
                "model_id": result.model_id,
                "events": [{"id": event.id, "action": event.action, "result": event.result, "payload": dict(event.payload)} for event in result.events]}, changed)
            if self.coordinator is not None:
                try:
                    self.coordinator.project_agent_results(tenant_id, project_id)
                except Exception:
                    # The Mission result is already durable. The next bounded
                    # worker tick repairs the Conversation from that source
                    # event; never misreport completed agent work as failed.
                    pass
            return completed
        except Exception:
            if started is None:
                raise
            return self.service.record_result(tenant_id, project_id, started.id, self.worker_id, agent.id, {"status": "failed"}, [])
