"""Executable OCI process contract for CMUL8 Cloud and private Docker Compose."""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import stat
import sys
import urllib.request
import threading
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlparse

WORKER_SOCKET = Path(os.environ.get("CMUL8_WORKER_SOCKET", "/tmp/cmul8-worker.sock"))
_MAX_DISCOVERY_STATE_BYTES = 8 * 1024 * 1024


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


def preflight() -> int:
	required = ("CMUL8_TENANT_ID", "CMUL8_ENVIRONMENT", "CMUL8_POSTGRES_URL", "CMUL8_REDIS_URL")
	missing = [key for key in required if not os.environ.get(key, "").strip()]
	if missing:
		print(f"missing required environment: {', '.join(missing)}", file=sys.stderr)
		return 78
	if os.environ.get("CMUL8_TLS_REQUIRED", "true").lower() != "true":
		print("private runtime requires CMUL8_TLS_REQUIRED=true", file=sys.stderr)
		return 78
	return 0


def _queue_reachable() -> bool:
	parsed = urlparse(os.environ.get("CMUL8_REDIS_URL", ""))
	if not parsed.hostname:
		return False
	try:
		with socket.create_connection((parsed.hostname, parsed.port or 6379), timeout=1):
			return True
	except OSError:
		return False


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
		return _queue_reachable()
	except OSError:
		return False


def _discovered_mission_workers(*, project_only: str | None = None, tenant_only: str | None = None) -> list[tuple[object, str, str]]:
    """Discover only self-consistent project-scoped Mission control files."""
    from simulacra.collaboration.models import validate_scope_id
    from simulacra.missions import JsonMissionRepository, MissionService, MissionWorker
    try:
        if project_only is not None: validate_scope_id(project_only, "project_id")
        if tenant_only is not None: validate_scope_id(tenant_only, "tenant_id")
    except ValueError:
        return []
    runs_root = Path(os.environ.get("SIMULACRA_RUNS_DIR", "/app/runs")).resolve()
    control_root = runs_root / ".mission-control"
    output: list[tuple[object, str, str]] = []
    if control_root.is_symlink() or not control_root.is_dir(): return output
    for state_path in sorted(control_root.glob("*/*/missions/state.json")):
        try:
            # Validate lexical components before any JSON read; discovery never grants
            # authority through a followed link.
            if state_path.is_symlink() or not state_path.is_file(): continue
            relative = state_path.relative_to(control_root)
            if len(relative.parts) != 4: continue
            tenant_id, project_id, missions, filename = relative.parts
            if missions != "missions" or filename != "state.json" or (project_only and project_id != project_only) or (tenant_only and tenant_id != tenant_only): continue
            lexical = control_root
            for component in (tenant_id, project_id, missions):
                lexical = lexical / component
                if lexical.is_symlink() or not lexical.is_dir(): raise ValueError("symlinked Mission control scope")
            if state_path.resolve(strict=True).parent != lexical.resolve(strict=True): raise ValueError("Mission state escapes control root")
            validate_scope_id(tenant_id, "tenant_id"); validate_scope_id(project_id, "project_id")
            state = _read_discovery_json(control_root, tuple(relative.parts))
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
            output.append((MissionWorker(MissionService(JsonMissionRepository(control_root)), workspace), tenant_id, project_id))
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
	def stop(_signum: int, _frame: object) -> None:
		nonlocal running
		running = False
		signal_active_codex_process_groups(signal.SIGTERM)
	signal.signal(signal.SIGTERM, stop)
	signal.signal(signal.SIGINT, stop)
	try:
		while running:
			if explicit_requested:
				workers = [explicit_worker] if explicit_worker is not None else []
			else:
				workers = _discovered_runtime_workers()
			for runtime_worker in workers:
				try:
					runtime_worker.run_once()
				except Exception:
					# Job-level failures are durably recorded by RuntimeWorker. A
					# transport failure must not terminate the process or forge ready.
					pass
			# Mission state is its own durable queue. Explicit project mode remains
			# confined to the configured project rather than discovering neighbors.
			configured_project = os.environ.get("CMUL8_PROJECT_ID", "").strip() or None
			configured_tenant = os.environ.get("CMUL8_TENANT_ID", "").strip() or None
			for mission_worker, tenant_id, project_id in _discovered_mission_workers(
				project_only=configured_project if explicit_requested else None,
				tenant_only=configured_tenant if explicit_requested else None,
			):
				key = (tenant_id, project_id)
				thread = mission_threads.get(key)
				if (thread is None or not thread.is_alive()) and len([item for item in mission_threads.values() if item.is_alive()]) < 2:
					thread = threading.Thread(target=mission_worker.run_once, args=(tenant_id, project_id), daemon=True, name=f"mission-{project_id[:24]}")
					mission_threads[key] = thread; thread.start()
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


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser(prog="cmul8-entrypoint")
	parser.add_argument("process", choices=("web", "api", "worker", "worker-health", "preflight", "migrations", "smoke"))
	parser.add_argument("process_args", nargs="*")
	args = parser.parse_args(argv)
	if args.process in {"web", "api"}:
		return serve(8080 if args.process == "web" else 8000)
	if args.process == "worker":
		return worker()
	if args.process == "worker-health":
		return worker_health(args.process_args[0] if args.process_args else "--live")
	if args.process == "preflight":
		return preflight()
	if args.process == "migrations":
		return migrations()
	return smoke()


if __name__ == "__main__":
	raise SystemExit(main())
