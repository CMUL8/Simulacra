"""Executable OCI process contract for CMUL8 Cloud and private Docker Compose."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request
import threading
from datetime import UTC, datetime
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

from deploy.readiness import assess_private_deployment, render_readiness_report

WORKER_SOCKET = Path(os.environ.get("CMUL8_WORKER_SOCKET", "/tmp/cmul8-worker.sock"))
_MAX_DISCOVERY_STATE_BYTES = 8 * 1024 * 1024
_DEFAULT_MISSION_CRON_INTERVAL_SECONDS = 15.0
_CERTIFIED_EXECUTION_BACKENDS: dict[str, object] = {"codex": lambda: None}


def bootstrap_recovery_tick(*, limit: int = 100) -> int:
	"""Resume a bounded number of interrupted Mission setups.

	This intentionally uses the same coordinator recovery path as an HTTP retry.
	It is safe to call on process start and never makes incomplete work visible.
	"""
	try:
		from simulacra.workplace.bootstrap_coordinator import WorkspaceBootstrapCoordinator
		return WorkspaceBootstrapCoordinator().recovery_tick(limit=min(100, max(1, limit)))
	except Exception:
		return 0


def _configured_mission_execution_backend() -> object | None:
	"""Create one reviewed, image-baked Mission executor.

	The registry is source-controlled and shipped in the deployment image. An
	environment variable can select a certified entry but can never import or
	execute arbitrary application-process code.
	"""
	from simulacra.harnesses import HarnessConfig
	from simulacra.missions.executor import MissionAgentExecutor

	backend_name = HarnessConfig.from_env().harness
	factory = _CERTIFIED_EXECUTION_BACKENDS.get(backend_name)
	if not callable(factory):
		raise ValueError("execution backend is not certified in this deployment image")
	backend = factory()
	if backend is None and backend_name == "codex":
		return None
	if not isinstance(backend, MissionAgentExecutor):
		raise ValueError("certified execution backend returned an invalid implementation")
	if backend.name != backend_name:
		raise ValueError("certified execution backend name does not match configuration")
	if backend.enforces_network_policy is not True:
		raise ValueError("certified execution backend must enforce the admitted network policy")
	return backend


def _mission_execution_configuration_ready() -> bool:
	try:
		_configured_mission_execution_backend()
		return True
	except (ImportError, AttributeError, TypeError, ValueError):
		return False


def _configured_notification_adapter() -> object | None:
	"""Build an explicitly configured real provider, never the test adapter."""
	specification = os.environ.get("SIMULACRA_NOTIFICATION_ADAPTER_FACTORY", "").strip()
	if not specification:
		return None
	module_name, separator, attribute_name = specification.partition(":")
	if not separator or not module_name or not attribute_name:
		return None
	try:
		from importlib import import_module
		from simulacra.collaboration.notifications import DeterministicNotificationAdapter

		factory = getattr(import_module(module_name), attribute_name)
		adapter = factory()
		if isinstance(adapter, DeterministicNotificationAdapter):
			return None
		if not callable(getattr(adapter, "deliver", None)):
			return None
		return adapter
	except Exception:
		return None


def _notification_tick(tenant_id: str, project_id: str) -> None:
	"""Bounded worker-only projection/delivery; never invoked by API routes."""
	try:
		from simulacra.collaboration.notifications import NotificationOutbox
		from simulacra.collaboration.repository import JsonCollaborationRepository
		from simulacra.demo.paths import RUNS_DIR
		from simulacra.workplace.preferences import JsonWorkplacePreferenceRepository
		control = RUNS_DIR / ".cmul8-control"
		outbox = NotificationOutbox(control / ".notifications")
		repository = JsonCollaborationRepository(control)
		preferences = JsonWorkplacePreferenceRepository(RUNS_DIR / ".workplace-control" / "preferences")
		outbox.project(repository, tenant_id=tenant_id, project_id=project_id, preferences=preferences)
		# Provider delivery is opt-in and requires a real adapter factory. Missing or
		# invalid configuration leaves rows pending for a later retry.
		if os.environ.get("SIMULACRA_NOTIFICATION_DELIVERY_ENABLED") == "1":
			adapter = _configured_notification_adapter()
			if adapter is not None:
				outbox.deliver(
					tenant_id=tenant_id, project_id=project_id, adapter=adapter,
					repository=repository, preferences=preferences,
				)
	except Exception:
		pass


def _mission_cron_interval_seconds() -> float:
	"""Return a bounded scheduler cadence even for malformed configuration."""
	try:
		configured = float(os.environ.get("CMUL8_MISSION_CRON_INTERVAL_SECONDS", "15"))
	except (TypeError, ValueError):
		configured = _DEFAULT_MISSION_CRON_INTERVAL_SECONDS
	return min(300.0, max(5.0, configured))


def _evaluate_mission_cron(mission_worker: object, tenant_id: str, project_id: str) -> None:
	"""Keep scheduler/graph failures from affecting worker liveness/readiness."""
	try:
		mission_worker.schedule_due_cron(tenant_id, project_id)  # type: ignore[attr-defined]
	except Exception:
		pass


def _mission_cron_scheduler(
	stop_event: threading.Event,
	*,
	project_only: str | None = None,
	tenant_only: str | None = None,
	discover: object | None = None,
	interval_seconds: float | None = None,
	initial_workers: list[tuple[object, str, str]] | None = None,
) -> None:
	"""Run cron discovery independently from job consumption and health probes."""
	discover_workers = discover or _discovered_mission_workers
	interval = _mission_cron_interval_seconds() if interval_seconds is None else max(0.01, interval_seconds)
	first = True
	while not stop_event.is_set():
		# A newly completed Mission may only be discovered after its bootstrap
		# journal has been repaired.  This bounded call also runs every scheduler
		# sweep, so a process that stays up heals interrupted setup without a
		# human retrying a request.
		bootstrap_recovery_tick()
		if first and initial_workers is not None:
			workers = initial_workers
		else:
			try:
				workers = discover_workers(  # type: ignore[operator]
					project_only=project_only,
					tenant_only=tenant_only,
				)
			except Exception:
				workers = []
		first = False
		for mission_worker, tenant_id, project_id in workers:
			if stop_event.is_set():
				break
			_evaluate_mission_cron(mission_worker, tenant_id, project_id)
		# Always wait after a sweep, including failed/slow sweeps. This prevents
		# malformed state from producing a retry hot loop while remaining promptly
		# stoppable on SIGTERM/SIGINT.
		if stop_event.wait(interval):
			break


def _read_discovery_json(root: Path, parts: tuple[str, ...]) -> Mapping[str, object]:
	"""Read one discovered state file through a no-follow descriptor walk.

	``glob`` is only an enumeration hint.  Every ancestor and the final file are
	opened relative to a still-open parent descriptor, so a swap between path
	validation and open cannot make a discovered Mission worker cross scopes.
	"""
	if not parts or any(part in {"", ".", ".."} or "/" in part for part in parts):
		raise ValueError("unsafe discovery path")
	flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
	directory_flag = getattr(os, "O_DIRECTORY", 0)
	try:
		root_fd = os.open(root, flags | directory_flag)
	except OSError as exc:
		raise ValueError("discovery root unavailable") from exc
	fds = [root_fd]
	try:
		if not stat.S_ISDIR(os.fstat(root_fd).st_mode):
			raise ValueError("unsafe discovery root")
		for part in parts[:-1]:
			child_fd = os.open(part, flags | directory_flag, dir_fd=fds[-1])
			if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
				os.close(child_fd); raise ValueError("unsafe discovery ancestor")
			fds.append(child_fd)
		file_fd = os.open(parts[-1], flags, dir_fd=fds[-1])
		try:
			info = os.fstat(file_fd)
			if not stat.S_ISREG(info.st_mode):
				raise ValueError("unsafe discovery state")
			chunks: list[bytes] = []; total = 0
			while True:
				chunk = os.read(file_fd, min(64 * 1024, _MAX_DISCOVERY_STATE_BYTES + 1 - total))
				if not chunk: break
				total += len(chunk)
				if total > _MAX_DISCOVERY_STATE_BYTES: raise ValueError("discovery state too large")
				chunks.append(chunk)
		finally:
			os.close(file_fd)
		value = json.loads(b"".join(chunks).decode("utf-8"))
		if not isinstance(value, Mapping): raise ValueError("invalid discovery state")
		return value
	finally:
		for descriptor in reversed(fds):
			os.close(descriptor)


def _service_reachable(url: str, *, default_port: int) -> bool:
	parsed = urlparse(url)
	if not parsed.hostname:
		return False
	try:
		with socket.create_connection((parsed.hostname, parsed.port or default_port), timeout=1):
			return True
	except OSError:
		return False


def _storage_roots_ready() -> bool:
	runs_root = Path(os.environ.get("SIMULACRA_RUNS_DIR", "/app/runs"))
	data_root = Path(os.environ.get("SIMULACRA_DATA_DIR", "/app/data"))
	mission_root = Path(os.environ.get("CMUL8_MISSION_RUNTIME_ROOT", str(data_root / "mission-runtime")))
	try:
		for root in (data_root, runs_root, mission_root):
			root.mkdir(parents=True, exist_ok=True)
			if root.is_symlink() or not root.is_dir() or not os.access(root, os.R_OK | os.W_OK):
				return False
		return True
	except OSError:
		return False


def private_readiness_report():
	return assess_private_deployment(
		dict(os.environ),
		probes={
			"database": lambda: _service_reachable(os.environ.get("CMUL8_POSTGRES_URL", ""), default_port=5432),
			"queue": _queue_reachable,
			"storage": _storage_roots_ready,
			"executor": _mission_execution_configuration_ready,
		},
	)


def preflight(report_format: str = "human") -> int:
	report = private_readiness_report()
	if report_format == "json":
		print(json.dumps(report.to_dict(), sort_keys=True))
	else:
		print(render_readiness_report(report))
	return 0 if report.startup_ready else 78


def doctor(report_format: str = "human") -> int:
	report = private_readiness_report()
	if report_format == "json":
		print(json.dumps(report.to_dict(), sort_keys=True))
	else:
		print(render_readiness_report(report))
	return 0 if report.production_ready else 2


def _queue_reachable() -> bool:
	return _service_reachable(os.environ.get("CMUL8_REDIS_URL", ""), default_port=6379)


def _configured_runtime_worker():
	"""Build the worker only from an explicitly configured approved revision.

	The durable filesystem repository is the job truth. Redis is deliberately
	limited to wakeup/readiness transport so a broker restart cannot lose jobs.
	"""
	project_id = os.environ.get("CMUL8_PROJECT_ID", "").strip()
	revision_hash = os.environ.get("CMUL8_OPERATION_GRAPH_REVISION", "").strip()
	tenant_id = os.environ.get("CMUL8_TENANT_ID", "").strip()
	environment_id = os.environ.get("CMUL8_ENVIRONMENT", "").strip()
	project_root = os.environ.get("CMUL8_PROJECT_ROOT", "").strip()
	if not all((project_id, revision_hash, tenant_id, environment_id, project_root)):
		raise ValueError("worker requires CMUL8_PROJECT_ID, CMUL8_PROJECT_ROOT, and CMUL8_OPERATION_GRAPH_REVISION")
	from simulacra.observability import JsonlTelemetryRepository
	from simulacra.operation_graph import OperationGraphStore
	from simulacra.operation_graph.errors import OperationGraphError
	from simulacra.runtime import RuntimePlane, RuntimeWorker
	runs_root = Path(os.environ.get("SIMULACRA_RUNS_DIR", "/app/runs"))
	runtime_root = Path(os.environ.get("CMUL8_RUNTIME_ROOT", str(runs_root / ".cmul8-runtime")))
	telemetry_root = Path(os.environ.get("CMUL8_TELEMETRY_ROOT", str(runs_root / ".cmul8-telemetry")))
	graph_store = OperationGraphStore(Path(project_root), tenant_id=tenant_id, project_id=project_id)
	plane = RuntimePlane.from_approved_revision(
		runtime_root, graph_store, revision_hash, environment_id=environment_id,
		observability_repository=JsonlTelemetryRepository(telemetry_root),
	)
	return RuntimeWorker(plane, os.environ.get("CMUL8_WORKER_ID", f"worker-{socket.gethostname()}"), queue_reachable=_queue_reachable)


def _discovered_runtime_workers() -> list[object]:
	"""Discover graph-bound queued scopes in the shared durable repository."""
	from simulacra.observability import JsonlTelemetryRepository
	from simulacra.operation_graph import OperationGraphStore
	from simulacra.operation_graph.errors import OperationGraphError
	from simulacra.runtime import RuntimePlane, RuntimeWorker
	from simulacra.runtime.errors import RuntimePlaneError

	runs_root = Path(os.environ.get("SIMULACRA_RUNS_DIR", "/app/runs")).resolve()
	runtime_root = Path(os.environ.get("CMUL8_RUNTIME_ROOT", str(runs_root / ".cmul8-runtime"))).resolve()
	telemetry_root = Path(os.environ.get("CMUL8_TELEMETRY_ROOT", str(runs_root / ".cmul8-telemetry"))).resolve()
	runtime_root.mkdir(parents=True, exist_ok=True)
	telemetry_root.mkdir(parents=True, exist_ok=True)
	workers: list[object] = []
	worker_prefix = os.environ.get("CMUL8_WORKER_ID", f"worker-{socket.gethostname()}")
	for state_path in sorted(runtime_root.glob("*/*/*/runtime/state.json")):
		try:
			relative = state_path.relative_to(runtime_root)
			if len(relative.parts) != 5:
				continue
			tenant_id, environment_id, project_id = relative.parts[:3]
			state = json.loads(state_path.read_text(encoding="utf-8"))
			if not isinstance(state, Mapping):
				continue
			if (state.get("tenant_id"), state.get("environment_id"), state.get("project_id")) != (
				tenant_id, environment_id, project_id,
			):
				continue
			jobs = state.get("jobs")
			if not isinstance(jobs, Mapping):
				continue
			revisions = {
				str(row.get("operation_graph_version") or "")
				for row in jobs.values()
				if isinstance(row, dict) and row.get("status") in {"queued", "running"}
			}
			project_root = (runs_root / project_id).resolve()
			if project_root.parent != runs_root or not project_root.is_dir():
				continue
			store = OperationGraphStore(project_root, tenant_id=tenant_id, project_id=project_id)
			for revision_hash in sorted(revisions - {""}):
				try:
					plane = RuntimePlane.from_approved_revision(
						runtime_root, store, revision_hash, environment_id=environment_id,
						observability_repository=JsonlTelemetryRepository(telemetry_root),
					)
					workers.append(RuntimeWorker(
						plane, f"{worker_prefix}-{project_id[:24]}-{revision_hash[:8]}",
						queue_reachable=_queue_reachable,
					))
				except (OSError, ValueError, OperationGraphError, RuntimePlaneError):
					# A stale/unapproved revision in this state must not prevent a
					# separately approved revision from receiving a worker.
					continue
		except (OSError, ValueError, json.JSONDecodeError, OperationGraphError, RuntimePlaneError):
			# A corrupt or stale scope cannot grant itself worker authority.
			continue
	return workers


def _runtime_roots_ready() -> bool:
	runs_root = Path(os.environ.get("SIMULACRA_RUNS_DIR", "/app/runs"))
	runtime_root = Path(os.environ.get("CMUL8_RUNTIME_ROOT", str(runs_root / ".cmul8-runtime")))
	telemetry_root = Path(os.environ.get("CMUL8_TELEMETRY_ROOT", str(runs_root / ".cmul8-telemetry")))
	try:
		for root in (runs_root, runtime_root, telemetry_root):
			root.mkdir(parents=True, exist_ok=True)
			if not os.access(root, os.R_OK | os.W_OK):
				return False
		return _queue_reachable() and _mission_execution_configuration_ready()
	except OSError:
		return False


def _discovered_mission_workers(*, project_only: str | None = None, tenant_only: str | None = None) -> list[tuple[object, str, str]]:
    """Discover only self-consistent project-scoped Mission control files."""
    from simulacra.collaboration import JsonCollaborationRepository
    from simulacra.collaboration.models import validate_scope_id
    from simulacra.missions import JsonMissionRepository, MissionService, MissionWorker
    from simulacra.workplace import AssignmentCoordinator
    try:
        if project_only is not None: validate_scope_id(project_only, "project_id")
        if tenant_only is not None: validate_scope_id(tenant_only, "tenant_id")
    except ValueError:
        return []
    try:
        # Validate deployment configuration before enumerating tenant work.
        # A fresh instance is still created for each worker below.
        _configured_mission_execution_backend()
    except (ImportError, AttributeError, TypeError, ValueError):
        return []
    runs_root = Path(os.environ.get("SIMULACRA_RUNS_DIR", "/app/runs")).resolve()
    control_root = runs_root / ".mission-control"
    output: list[tuple[object, str, str]] = []
    if control_root.is_symlink() or not control_root.is_dir(): return output
    indexes = list(control_root.glob("*/*/missions/discovery.json"))
    legacy_states = [
        path for path in control_root.glob("*/*/missions/state.json")
        if not (path.parent / "discovery.json").exists()
    ]
    for state_path in sorted([*indexes, *legacy_states]):
        try:
            # Validate lexical components before any JSON read; discovery never grants
            # authority through a followed link.
            if state_path.is_symlink() or not state_path.is_file(): continue
            relative = state_path.relative_to(control_root)
            if len(relative.parts) != 4: continue
            tenant_id, project_id, missions, filename = relative.parts
            if missions != "missions" or filename not in {"discovery.json", "state.json"} or (project_only and project_id != project_only) or (tenant_only and tenant_id != tenant_only): continue
            lexical = control_root
            for component in (tenant_id, project_id, missions):
                lexical = lexical / component
                if lexical.is_symlink() or not lexical.is_dir(): raise ValueError("symlinked Mission control scope")
            if state_path.resolve(strict=True).parent != lexical.resolve(strict=True): raise ValueError("Mission state escapes control root")
            validate_scope_id(tenant_id, "tenant_id"); validate_scope_id(project_id, "project_id")
            state = _read_discovery_json(control_root, tuple(relative.parts))
            if filename == "discovery.json":
                if (state.get("tenant_id"), state.get("project_id")) != (tenant_id, project_id): continue
                if state.get("schema_version") != 1 or not isinstance(state.get("mission_id"), str): continue
                full_state = lexical / "state.json"
                if full_state.is_symlink() or not full_state.is_file(): continue
            else:
                # One-release migration path. Legacy bounded states can be
                # discovered once; their next repository mutation publishes
                # the permanent tiny index. Oversized state is never read here.
                if not isinstance(state.get("mission"), Mapping): continue
                mission = state["mission"]
                if (mission.get("tenant_id"), mission.get("project_id")) != (tenant_id, project_id): continue
            workspace_path = runs_root / project_id
            if workspace_path.is_symlink() or not workspace_path.is_dir(): continue
            workspace = workspace_path.resolve(strict=True)
            if workspace.parent != runs_root or workspace != workspace_path: continue
            workspace_state = workspace_path / "state.json"
            if workspace_state.is_symlink() or not workspace_state.is_file(): continue
            try: workspace_record = _read_discovery_json(workspace, ("state.json",))
            except (OSError, ValueError, json.JSONDecodeError): continue
            if workspace_record.get("id") != project_id or workspace_record.get("tenant_id") != tenant_id: continue
            service = MissionService(JsonMissionRepository(control_root))
            coordinator = AssignmentCoordinator(
                JsonCollaborationRepository(runs_root / ".cmul8-control"), service, workspace,
                runs_root=runs_root, clock=lambda: datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            )
            execution_backend = _configured_mission_execution_backend()
            output.append((MissionWorker(
                service,
                workspace,
                coordinator=coordinator,
                execution_backend=execution_backend,
            ), tenant_id, project_id))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return output


def worker() -> int:
	from simulacra.harnesses.codex import signal_active_codex_process_groups
	WORKER_SOCKET.unlink(missing_ok=True)
	server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	server.bind(str(WORKER_SOCKET))
	WORKER_SOCKET.chmod(0o600)
	server.listen(8)
	server.settimeout(1)
	running = True
	# Daemon workers deliberately cannot hold SIGTERM hostage while an external
	# Codex turn is blocked. Scope dedupe below keeps this bounded at two.
	mission_threads: dict[tuple[str, str], threading.Thread] = {}
	explicit_worker = None
	explicit_requested = bool(os.environ.get("CMUL8_PROJECT_ID", "").strip())
	if explicit_requested:
		from simulacra.operation_graph.errors import OperationGraphError
		from simulacra.runtime.errors import RuntimePlaneError
		try:
			explicit_worker = _configured_runtime_worker()
		except (OSError, ValueError, OperationGraphError, RuntimePlaneError):
			# Explicit confinement must not silently fall back to unrelated projects.
			pass
	configured_project = os.environ.get("CMUL8_PROJECT_ID", "").strip() or None
	configured_tenant = os.environ.get("CMUL8_TENANT_ID", "").strip() or None
	# Never discover or claim a Mission until recoverable setup has had one
	# bounded chance to finish.  This preserves the graph/source contract for a
	# worker started immediately after an API restart.
	bootstrap_recovery_tick()
	# Import/discover both planes serially before any scheduler thread starts.
	# This prevents Python import-lock contention during process startup.
	if explicit_requested:
		initial_runtime_workers = [explicit_worker] if explicit_worker is not None else []
	else:
		initial_runtime_workers = _discovered_runtime_workers()
	initial_mission_workers = _discovered_mission_workers(
		project_only=configured_project if explicit_requested else None,
		tenant_only=configured_tenant if explicit_requested else None,
	)
	cron_stop = threading.Event()
	cron_thread = threading.Thread(
		target=_mission_cron_scheduler,
		kwargs={
			"stop_event": cron_stop,
			"project_only": configured_project if explicit_requested else None,
			"tenant_only": configured_tenant if explicit_requested else None,
			"initial_workers": initial_mission_workers,
		},
		daemon=True,
		name="mission-cron-scheduler",
	)
	cron_thread.start()
	def stop(_signum: int, _frame: object) -> None:
		nonlocal running
		running = False
		cron_stop.set()
		signal_active_codex_process_groups(signal.SIGTERM)
	signal.signal(signal.SIGTERM, stop)
	signal.signal(signal.SIGINT, stop)
	try:
		first_worker_iteration = True
		while running:
			workers = initial_runtime_workers if first_worker_iteration else (
				[explicit_worker] if explicit_requested and explicit_worker is not None else []
				if explicit_requested else _discovered_runtime_workers()
			)
			for runtime_worker in workers:
				try:
					runtime_worker.run_once()
				except Exception:
					# Job-level failures are durably recorded by RuntimeWorker. A
					# transport failure must not terminate the process or forge ready.
					pass
			# Mission state is its own durable queue. Explicit project mode remains
			# confined to the configured project rather than discovering neighbors.
			mission_workers = initial_mission_workers if first_worker_iteration else _discovered_mission_workers(
				project_only=configured_project if explicit_requested else None,
				tenant_only=configured_tenant if explicit_requested else None,
			)
			first_worker_iteration = False
			for mission_worker, tenant_id, project_id in mission_workers:
				key = (tenant_id, project_id)
				thread = mission_threads.get(key)
				if (thread is None or not thread.is_alive()) and len([item for item in mission_threads.values() if item.is_alive()]) < 2:
					thread = threading.Thread(target=mission_worker.run_once, args=(tenant_id, project_id), daemon=True, name=f"mission-{project_id[:24]}")
					mission_threads[key] = thread; thread.start()
				_notification_tick(tenant_id, project_id)
			for key, thread in list(mission_threads.items()):
				if not thread.is_alive(): mission_threads.pop(key, None)
			try:
				connection, _ = server.accept()
			except TimeoutError:
				continue
			try:
				with connection:
					request = connection.recv(32).decode("ascii", "replace").strip()
					ready = request == "LIVE"
					if request == "READY":
						ready = _runtime_roots_ready()
						if explicit_requested:
							ready = ready and explicit_worker is not None and explicit_worker.readiness()["status"] == "ready"
					connection.sendall(b"OK\n" if ready else b"NOT_READY\n")
			except OSError:
				# A probe client may disconnect before receiving its response; that
				# cannot be allowed to kill the durable worker process.
				continue
	finally:
		cron_stop.set()
		cron_thread.join(timeout=2)
		signal_active_codex_process_groups(signal.SIGKILL)
		server.close()
		WORKER_SOCKET.unlink(missing_ok=True)
	return 0


def worker_health(mode: str) -> int:
	request = "READY" if mode == "--ready" else "LIVE"
	try:
		with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
			client.settimeout(2)
			client.connect(str(WORKER_SOCKET))
			client.sendall(f"{request}\n".encode())
			return 0 if client.recv(32).startswith(b"OK") else 1
	except OSError:
		return 1


def migrations() -> int:
	if preflight():
		return 78
	from simulacra.demo.db import migrate
	migrate()
	return 0


def smoke() -> int:
	base = os.environ.get("CMUL8_SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
	try:
		with urllib.request.urlopen(f"{base}/readyz", timeout=5) as response:
			return 0 if response.status == 200 else 1
	except OSError:
		return 1


def serve(port: int) -> int:
	os.execvp("uvicorn", ["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", str(port)])
	return 70


def _stop_child(process: subprocess.Popen[bytes], timeout: float = 5.0) -> None:
	"""Stop an isolated child process group without leaving Codex descendants."""
	def group_exists() -> bool:
		try:
			os.killpg(process.pid, 0)
			return True
		except ProcessLookupError:
			return False

	try:
		os.killpg(process.pid, signal.SIGTERM)
	except ProcessLookupError:
		pass
	deadline = time.monotonic() + timeout
	while group_exists() and time.monotonic() < deadline:
		if process.poll() is None:
			try:
				process.wait(timeout=min(0.1, max(0.01, deadline - time.monotonic())))
			except subprocess.TimeoutExpired:
				pass
		else:
			threading.Event().wait(0.05)
	if group_exists():
		try:
			os.killpg(process.pid, signal.SIGKILL)
		except ProcessLookupError:
			pass
	if process.poll() is None:
		process.wait(timeout=timeout)


def serve_with_worker(port: int = 8080) -> int:
	"""Supervise the public API and worker in one volume-sharing container.

	Railway volumes attach to a service instance. Keeping these processes in one
	container gives the worker the exact durable Mission queue used by the API.
	If either process exits, the supervisor stops the other and lets Railway
	restart the complete service rather than serving a misleading half-live app.
	"""
	worker_process = subprocess.Popen(
		[sys.executable, "-m", "simulacra.deploy_process", "worker"],
		start_new_session=True,
	)
	web_process = subprocess.Popen(
		["uvicorn", "apps.api.main:app", "--host", "0.0.0.0", "--port", str(port)],
		start_new_session=True,
	)
	children = (web_process, worker_process)
	stop_event = threading.Event()

	def stop(_signum: int, _frame: object) -> None:
		stop_event.set()

	signal.signal(signal.SIGTERM, stop)
	signal.signal(signal.SIGINT, stop)
	exit_code = 0
	try:
		while not stop_event.wait(0.2):
			for child in children:
				code = child.poll()
				if code is not None:
					exit_code = code or 1
					stop_event.set()
					break
	finally:
		for child in reversed(children):
			_stop_child(child)
	return exit_code


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(prog="cmul8-entrypoint")
	parser.add_argument("process", choices=("web", "web-worker", "api", "worker", "worker-health", "preflight", "doctor", "migrations", "smoke"))
	parser.add_argument("process_args", nargs="*")
	parser.add_argument("--format", dest="report_format", choices=("human", "json"), default="human")
	health_mode = parser.add_mutually_exclusive_group()
	health_mode.add_argument("--ready", action="store_true")
	health_mode.add_argument("--live", action="store_true")
	args = parser.parse_args(argv)
	if args.process in {"web", "api"}:
		return serve(8080 if args.process == "web" else 8000)
	if args.process == "web-worker":
		return serve_with_worker(8080)
	if args.process == "worker":
		return worker()
	if args.process == "worker-health":
		mode = "--ready" if args.ready else "--live"
		return worker_health(mode)
	if args.process == "preflight":
		return preflight(args.report_format)
	if args.process == "doctor":
		return doctor(args.report_format)
	if args.process == "migrations":
		return migrations()
	return smoke()


if __name__ == "__main__":
	raise SystemExit(main())
