from __future__ import annotations

import socket
import json
import threading
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

from simulacra import deploy_process
from apps.api.main import liveness, readiness
from simulacra.operation_graph import OperationGraphStore, load_operation_graph
from simulacra.runtime import RuntimePlane
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.workplace import AssignmentCoordinator


def test_preflight_rejects_missing_contract(monkeypatch):
	for key in ("CMUL8_TENANT_ID", "CMUL8_ENVIRONMENT", "CMUL8_POSTGRES_URL", "CMUL8_REDIS_URL"):
		monkeypatch.delenv(key, raising=False)
	assert deploy_process.preflight() == 78


def test_preflight_accepts_explicit_external_services(monkeypatch):
	values = {
		"CMUL8_TENANT_ID": "tenant", "CMUL8_ENVIRONMENT": "production",
		"CMUL8_POSTGRES_URL": "postgres://db/app", "CMUL8_REDIS_URL": "redis://queue/0",
		"CMUL8_TLS_REQUIRED": "true",
	}
	for key, value in values.items():
		monkeypatch.setenv(key, value)
	assert deploy_process.preflight() == 0


def test_mission_cron_cadence_is_bounded_and_scheduler_failures_are_contained(monkeypatch):
	for configured, expected in (("1", 5.0), ("45", 45.0), ("900", 300.0), ("invalid", 15.0)):
		monkeypatch.setenv("CMUL8_MISSION_CRON_INTERVAL_SECONDS", configured)
		assert deploy_process._mission_cron_interval_seconds() == expected

	class BrokenScheduler:
		def schedule_due_cron(self, tenant_id, project_id):
			assert (tenant_id, project_id) == ("tenant", "project")
			raise RuntimeError("one Mission must not kill readiness")

	assert deploy_process._evaluate_mission_cron(BrokenScheduler(), "tenant", "project") is None


def test_mission_cron_scheduler_ticks_and_stops_while_runtime_work_is_blocked():
	stop = threading.Event()
	ticks = threading.Event()
	runtime_release = threading.Event()
	calls: list[tuple[str | None, str | None]] = []

	class Scheduler:
		def schedule_due_cron(self, _tenant, _project):
			if len(calls) >= 2:
				ticks.set()

	def discover(*, project_only=None, tenant_only=None):
		calls.append((project_only, tenant_only))
		return [(Scheduler(), "tenant", "project")]

	runtime_thread = threading.Thread(target=lambda: runtime_release.wait(2), daemon=True)
	scheduler_thread = threading.Thread(
		target=deploy_process._mission_cron_scheduler,
		kwargs={
			"stop_event": stop,
			"project_only": "project",
			"tenant_only": "tenant",
			"discover": discover,
			"interval_seconds": 0.01,
		},
		daemon=True,
	)
	runtime_thread.start(); scheduler_thread.start()
	try:
		assert ticks.wait(1) and runtime_thread.is_alive()
		assert len(calls) >= 2 and set(calls) == {("project", "tenant")}
	finally:
		stop.set(); scheduler_thread.join(1)
		runtime_release.set(); runtime_thread.join(1)
	assert not scheduler_thread.is_alive()


def test_worker_serializes_initial_discovery_before_scheduler_and_reuses_it(monkeypatch, tmp_path):
	"""Startup imports both worker planes before any scheduler execution begins."""
	events: list[str] = []
	handlers: dict[int, object] = {}
	socket_path = tmp_path / "worker.sock"

	class RuntimeWorker:
		def __init__(self, name: str) -> None:
			self.name = name

		def run_once(self) -> None:
			events.append(f"runtime_run:{self.name}")

	class MissionWorker:
		def __init__(self, name: str) -> None:
			self.name = name

		def schedule_due_cron(self, _tenant: str, _project: str) -> None:
			events.append(f"mission_cron:{self.name}")

		def run_once(self, _tenant: str, _project: str) -> None:
			events.append(f"mission_run:{self.name}")

	initial_runtime, later_runtime = RuntimeWorker("initial"), RuntimeWorker("later")
	initial_mission, later_mission = MissionWorker("initial"), MissionWorker("later")
	runtime_discoveries = 0
	mission_discoveries = 0

	def discover_runtime():
		nonlocal runtime_discoveries
		runtime_discoveries += 1
		events.append(f"runtime_discovery:{runtime_discoveries}")
		return [initial_runtime] if runtime_discoveries == 1 else [later_runtime]

	def discover_mission(*, project_only=None, tenant_only=None):
		nonlocal mission_discoveries
		assert project_only is None and tenant_only is None
		mission_discoveries += 1
		events.append(f"mission_discovery:{mission_discoveries}")
		return [(initial_mission, "tenant", "project")] if mission_discoveries == 1 else [(later_mission, "tenant", "project")]

	class ControlledStop:
		def __init__(self) -> None:
			self.waits = 0

		def is_set(self) -> bool:
			return False

		def wait(self, _timeout: float) -> bool:
			self.waits += 1
			return self.waits >= 2

		def set(self) -> None:
			events.append("scheduler_stop")

	class ImmediateThread:
		def __init__(self, *, target, args=(), kwargs=None, **_ignored) -> None:
			self.target = target
			self.args = args
			self.kwargs = kwargs or {}

		def start(self) -> None:
			if self.target is deploy_process._mission_cron_scheduler:
				events.append("scheduler_start")
				assert self.kwargs["initial_workers"] == [(initial_mission, "tenant", "project")]
				events.append("scheduler_received_initial")
			self.target(*self.args, **self.kwargs)

		def is_alive(self) -> bool:
			return False

		def join(self, timeout=None) -> None:
			return None

	class Connection:
		def recv(self, _size: int) -> bytes:
			return b"LIVE\n"

		def sendall(self, value: bytes) -> None:
			assert value == b"OK\n"

		def __enter__(self):
			return self

		def __exit__(self, *_args) -> None:
			return None

	class Server:
		def __init__(self) -> None:
			self.accepts = 0

		def bind(self, path: str) -> None:
			Path(path).touch()

		def listen(self, _backlog: int) -> None:
			return None

		def settimeout(self, _timeout: float) -> None:
			return None

		def accept(self):
			self.accepts += 1
			if self.accepts == 1:
				raise TimeoutError
			handlers[signal.SIGTERM](None, None)  # type: ignore[operator]
			return Connection(), None

		def close(self) -> None:
			return None

	from simulacra.harnesses import codex as codex_harness
	monkeypatch.setattr(deploy_process, "WORKER_SOCKET", socket_path)
	monkeypatch.setattr(deploy_process.socket, "socket", lambda *_args: Server())
	monkeypatch.setattr(deploy_process.threading, "Event", ControlledStop)
	monkeypatch.setattr(deploy_process.threading, "Thread", ImmediateThread)
	monkeypatch.setattr(deploy_process.signal, "signal", lambda number, handler: handlers.__setitem__(number, handler))
	monkeypatch.setattr(codex_harness, "signal_active_codex_process_groups", lambda _signal: events.append("signal_groups"))
	monkeypatch.setattr(deploy_process, "_discovered_runtime_workers", discover_runtime)
	monkeypatch.setattr(deploy_process, "_discovered_mission_workers", discover_mission)

	assert deploy_process.worker() == 0
	assert events.index("runtime_discovery:1") < events.index("scheduler_start")
	assert events.index("mission_discovery:1") < events.index("scheduler_start")
	assert events.index("scheduler_received_initial") < events.index("mission_cron:initial") < events.index("mission_discovery:2")
	assert events.index("runtime_run:initial") < events.index("runtime_discovery:2")
	assert "mission_run:initial" in events
	assert "runtime_run:later" in events and "mission_run:later" in events


def test_worker_health_probes_a_running_worker_socket(monkeypatch, tmp_path):
	# AF_UNIX paths are capped at ~104 bytes on macOS; pytest's nested tmp_path
	# can exceed that before the socket name is appended.
	socket_path = deploy_process.Path(f"/private/tmp/cm8-health-{os.getpid()}.sock")
	socket_path.unlink(missing_ok=True)
	monkeypatch.setattr(deploy_process, "WORKER_SOCKET", socket_path)
	server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	try:
		server.bind(str(socket_path))
	except PermissionError:
		server.close()
		pytest.skip("managed sandbox forbids AF_UNIX bind")
	server.listen(1)

	def respond():
		connection, _ = server.accept()
		with connection:
			assert connection.recv(32).strip() == b"LIVE"
			connection.sendall(b"OK\n")
		server.close()

	thread = threading.Thread(target=respond)
	thread.start()
	assert deploy_process.worker_health("--live") == 0
	thread.join(timeout=2)
	assert not thread.is_alive()
	socket_path.unlink(missing_ok=True)


def test_real_worker_serves_live_ready_and_a_second_probe(monkeypatch, tmp_path):
	"""Exercise the worker loop with real AF_UNIX connection lifetimes."""
	socket_path = deploy_process.Path(f"/private/tmp/cm8-worker-{os.getpid()}-{time.time_ns()}.sock")
	probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	try:
		try:
			probe.bind(str(socket_path))
		except PermissionError:
			pytest.skip("managed sandbox forbids AF_UNIX bind")
	finally:
		probe.close()
		socket_path.unlink(missing_ok=True)
	monkeypatch.setattr(deploy_process, "WORKER_SOCKET", socket_path)
	queue_server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
	queue_server.bind(("127.0.0.1", 0))
	queue_server.listen(4)
	queue_server.settimeout(0.1)
	stop_queue = threading.Event()

	def accept_queue_connections() -> None:
		while not stop_queue.is_set():
			try:
				connection, _ = queue_server.accept()
			except TimeoutError:
				continue
			except OSError:
				break
			with connection:
				pass

	queue_thread = threading.Thread(target=accept_queue_connections)
	queue_thread.start()
	queue_port = queue_server.getsockname()[1]
	runtime_root = tmp_path / "runtime"
	invalid_state = runtime_root / "tenant_invalid" / "environment_invalid" / "project_invalid" / "runtime" / "state.json"
	invalid_state.parent.mkdir(parents=True)
	invalid_state.write_text("[]", encoding="utf-8")
	environment = {
		**os.environ,
		"CMUL8_WORKER_SOCKET": str(socket_path),
		"SIMULACRA_RUNS_DIR": str(tmp_path / "runs"),
		"CMUL8_RUNTIME_ROOT": str(runtime_root),
		"CMUL8_TELEMETRY_ROOT": str(tmp_path / "telemetry"),
		"CMUL8_REDIS_URL": f"redis://127.0.0.1:{queue_port}/0",
	}
	process = subprocess.Popen(
		[sys.executable, "-c", "from simulacra.deploy_process import worker; raise SystemExit(worker())"],
		cwd=deploy_process.Path(__file__).parents[1],
		env=environment,
		stdout=subprocess.DEVNULL,
		stderr=subprocess.PIPE,
	)
	try:
		deadline = time.monotonic() + 5
		while not socket_path.exists() and process.poll() is None and time.monotonic() < deadline:
			time.sleep(0.02)
		if process.poll() is not None:
			detail = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
			raise AssertionError(f"worker exited before accepting probes: {detail}")
		assert socket_path.exists(), "worker did not create its health socket"
		live_deadline = time.monotonic() + 5
		while time.monotonic() < live_deadline:
			if process.poll() is not None:
				detail = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
				raise AssertionError(f"worker exited before serving LIVE: {detail}")
			if deploy_process.worker_health("--live") == 0:
				break
			time.sleep(0.02)
		else:
			raise AssertionError("worker did not serve LIVE before deadline")
		assert deploy_process.worker_health("--ready") == 0
		assert deploy_process.worker_health("--live") == 0
	finally:
		if process.poll() is None:
			process.send_signal(signal.SIGTERM)
			try:
				process.wait(timeout=3)
			except subprocess.TimeoutExpired:
				process.kill()
				process.wait(timeout=3)
		if process.stderr:
			process.stderr.close()
		stop_queue.set()
		queue_server.close()
		queue_thread.join(timeout=2)
		socket_path.unlink(missing_ok=True)


def test_image_defines_the_process_entrypoint():
	repository = deploy_process.Path(__file__).parents[1]
	dockerfile = (repository / "Dockerfile").read_text()
	entrypoint = (repository / "deploy/bin/cmul8-entrypoint").read_text()
	assert 'ENTRYPOINT ["/opt/cmul8/bin/cmul8-entrypoint"]' in dockerfile
	assert 'CMD ["api"]' in dockerfile
	assert "worker-health" in dockerfile
	assert "@openai/codex@0.148.0" in dockerfile
	assert "gosu" in dockerfile
	assert "lost+found" in entrypoint
	assert 'exec gosu 65532:65532 "$0" "$@"' in entrypoint


def test_railway_uses_the_public_web_process_port():
	railway = (deploy_process.Path(__file__).parents[1] / "railway.toml").read_text()
	assert 'startCommand = "/opt/cmul8/bin/cmul8-web-worker"' in railway
	assert 'healthcheckPath = "/health"' in railway


def test_web_worker_process_dispatches_the_combined_supervisor(monkeypatch):
	seen: list[int] = []
	monkeypatch.setattr(deploy_process, "serve_with_worker", lambda port: seen.append(port) or 23)
	assert deploy_process.main(["web-worker"]) == 23
	assert seen == [8080]


def test_compose_api_and_worker_share_mounted_runtime_roots():
	compose = yaml.safe_load((deploy_process.Path(__file__).parents[1] / "docker-compose.yml").read_text())
	environment = compose["x-cmul8-environment"]
	assert environment["CMUL8_RUNTIME_ROOT"] == "/app/runs/.cmul8-runtime"
	assert environment["CMUL8_TELEMETRY_ROOT"] == "/app/runs/.cmul8-telemetry"
	assert compose["services"]["api"]["command"] == ["api"]
	assert compose["services"]["worker"]["command"] == ["worker"]


def test_api_exposes_container_health_contract():
	assert liveness()["status"] == "live"
	assert readiness()["status"] == "ready"


def test_compose_worker_discovers_and_executes_shared_project_jobs(monkeypatch, tmp_path):
	runs = tmp_path / "runs"
	project = runs / "project_support"
	project.mkdir(parents=True)
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	graph = load_operation_graph(deploy_process.Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	revision = store.create_revision(graph, expected_revision_hash=None)
	store.approve_revision(revision.revision_hash, actor_id="owner")
	runtime_root = runs / ".cmul8-runtime"
	plane = RuntimePlane.from_approved_revision(
		runtime_root, store, revision.revision_hash, environment_id="production",
	)
	workflow = plane.workflows.start("workflow_resolve_case")
	job = plane.scheduler.enqueue("workflow.transition", {
		"instance_id": workflow.id, "target_state": "triaged", "expected_state": "new",
		"expected_revision": workflow.revision,
	})
	monkeypatch.setenv("SIMULACRA_RUNS_DIR", str(runs))
	monkeypatch.setenv("CMUL8_RUNTIME_ROOT", str(runtime_root))
	monkeypatch.setenv("CMUL8_TELEMETRY_ROOT", str(runs / ".cmul8-telemetry"))
	monkeypatch.setattr(deploy_process, "_queue_reachable", lambda: True)

	workers = deploy_process._discovered_runtime_workers()

	assert len(workers) == 1
	completed = workers[0].run_once()
	assert completed is not None and completed.id == job.id and completed.status == "succeeded"
	assert deploy_process._runtime_roots_ready() is True


def test_worker_discovery_skips_a_stale_revision_without_blocking_a_valid_scope(monkeypatch, tmp_path):
	runs = tmp_path / "runs"
	project = runs / "project_support"
	project.mkdir(parents=True)
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	graph = load_operation_graph(deploy_process.Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	revision = store.create_revision(graph, expected_revision_hash=None)
	store.approve_revision(revision.revision_hash, actor_id="owner")
	runtime_root = runs / ".cmul8-runtime"
	plane = RuntimePlane.from_approved_revision(runtime_root, store, revision.revision_hash, environment_id="production")
	workflow = plane.workflows.start("workflow_resolve_case")
	job = plane.scheduler.enqueue("workflow.transition", {
		"instance_id": workflow.id, "target_state": "triaged", "expected_state": "new",
		"expected_revision": workflow.revision,
	})

	def inject_stale_revision(state: dict) -> None:
		stale = dict(state["jobs"][job.id])
		stale["id"] = "job_stale_revision"
		stale["operation_graph_version"] = "0" * 64
		state["jobs"][stale["id"]] = stale

	plane.repository.mutate_project("tenant_acme", "production", "project_support", inject_stale_revision)
	monkeypatch.setenv("SIMULACRA_RUNS_DIR", str(runs))
	monkeypatch.setenv("CMUL8_RUNTIME_ROOT", str(runtime_root))
	monkeypatch.setenv("CMUL8_TELEMETRY_ROOT", str(runs / ".cmul8-telemetry"))
	monkeypatch.setattr(deploy_process, "_queue_reachable", lambda: True)

	workers = deploy_process._discovered_runtime_workers()

	assert len(workers) == 1
	completed = workers[0].run_once()
	assert completed is not None and completed.id == job.id and completed.status == "succeeded"
	assert plane.workflows.get(workflow.id).state == "triaged"


def test_worker_discovery_skips_invalid_json_container_shapes_without_blocking_valid_jobs(monkeypatch, tmp_path):
	runs = tmp_path / "runs"
	project = runs / "project_support"
	project.mkdir(parents=True)
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	graph = load_operation_graph(deploy_process.Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	revision = store.create_revision(graph, expected_revision_hash=None)
	store.approve_revision(revision.revision_hash, actor_id="owner")
	runtime_root = runs / ".cmul8-runtime"
	plane = RuntimePlane.from_approved_revision(runtime_root, store, revision.revision_hash, environment_id="production")
	workflow = plane.workflows.start("workflow_resolve_case")
	job = plane.scheduler.enqueue("workflow.transition", {
		"instance_id": workflow.id, "target_state": "triaged", "expected_state": "new",
		"expected_revision": workflow.revision,
	})
	for project_id, payload in (("invalid_array", "[]"), ("invalid_jobs", '{"jobs": []}')):
		state_path = runtime_root / "tenant_000" / "environment_000" / project_id / "runtime" / "state.json"
		state_path.parent.mkdir(parents=True)
		state_path.write_text(payload, encoding="utf-8")
	monkeypatch.setenv("SIMULACRA_RUNS_DIR", str(runs))
	monkeypatch.setenv("CMUL8_RUNTIME_ROOT", str(runtime_root))
	monkeypatch.setenv("CMUL8_TELEMETRY_ROOT", str(runs / ".cmul8-telemetry"))
	monkeypatch.setattr(deploy_process, "_queue_reachable", lambda: True)

	workers = deploy_process._discovered_runtime_workers()

	assert len(workers) == 1
	completed = workers[0].run_once()
	assert completed is not None and completed.id == job.id and completed.status == "succeeded"
	assert plane.workflows.get(workflow.id).state == "triaged"


def test_invalid_explicit_worker_config_never_falls_back_to_discovery(monkeypatch, tmp_path):
	socket_path = tmp_path / "worker.sock"
	responses: list[bytes] = []
	handlers: dict[int, object] = {}
	discovery_calls: list[bool] = []

	class Connection:
		closed = False

		def recv(self, _size: int) -> bytes:
			return b"READY\n"

		def sendall(self, value: bytes) -> None:
			responses.append(value)

		def __enter__(self):
			return self

		def __exit__(self, *_args) -> None:
			self.closed = True
			return None

	class Server:
		connection: Connection | None = None

		def bind(self, path: str) -> None:
			deploy_process.Path(path).touch()

		def listen(self, _backlog: int) -> None:
			return None

		def settimeout(self, _timeout: float) -> None:
			return None

		def accept(self):
			handler = handlers[signal.SIGTERM]
			handler(None, None)  # type: ignore[operator]
			self.connection = Connection()
			return self.connection, None

		def close(self) -> None:
			return None

	monkeypatch.setattr(deploy_process, "WORKER_SOCKET", socket_path)
	server = Server()
	monkeypatch.setattr(deploy_process.socket, "socket", lambda *_args: server)
	monkeypatch.setattr(deploy_process.signal, "signal", lambda signal_number, handler: handlers.__setitem__(signal_number, handler))
	monkeypatch.setattr(deploy_process, "_configured_runtime_worker", lambda: (_ for _ in ()).throw(ValueError("invalid explicit scope")))
	monkeypatch.setattr(deploy_process, "_discovered_runtime_workers", lambda: discovery_calls.append(True) or [])
	monkeypatch.setattr(deploy_process, "_runtime_roots_ready", lambda: True)
	monkeypatch.setenv("CMUL8_PROJECT_ID", "project_confined")

	assert deploy_process.worker() == 0
	assert discovery_calls == []
	assert responses == [b"NOT_READY\n"]
	assert server.connection is not None and server.connection.closed


def test_worker_survives_a_probe_io_failure_and_closes_every_test_connection(monkeypatch, tmp_path):
	socket_path = tmp_path / "worker.sock"
	handlers: dict[int, object] = {}
	responses: list[bytes] = []

	class Connection:
		def __init__(self, *, fails: bool) -> None:
			self.fails = fails
			self.closed = False

		def recv(self, _size: int) -> bytes:
			if self.fails:
				raise OSError("client disconnected")
			return b"LIVE\n"

		def sendall(self, value: bytes) -> None:
			responses.append(value)

		def __enter__(self):
			return self

		def __exit__(self, *_args) -> None:
			self.closed = True
			return None

	class Server:
		def __init__(self) -> None:
			self.all_connections = [Connection(fails=True), Connection(fails=False)]
			self.connections = list(self.all_connections)

		def bind(self, path: str) -> None:
			deploy_process.Path(path).touch()

		def listen(self, _backlog: int) -> None:
			return None

		def settimeout(self, _timeout: float) -> None:
			return None

		def accept(self):
			connection = self.connections.pop(0)
			if not connection.fails:
				handlers[signal.SIGTERM](None, None)  # type: ignore[operator]
			return connection, None

		def close(self) -> None:
			return None

	server = Server()
	monkeypatch.setattr(deploy_process, "WORKER_SOCKET", socket_path)
	monkeypatch.setattr(deploy_process.socket, "socket", lambda *_args: server)
	monkeypatch.setattr(deploy_process.signal, "signal", lambda signal_number, handler: handlers.__setitem__(signal_number, handler))
	monkeypatch.setattr(deploy_process, "_discovered_runtime_workers", lambda: [])

	assert deploy_process.worker() == 0
	assert responses == [b"OK\n"]
	assert all(connection.closed for connection in server.all_connections)


def test_mission_discovery_refuses_links_mismatches_and_honors_project_filter(monkeypatch, tmp_path):
	runs = tmp_path / "runs"; runs.mkdir(); control = runs / ".mission-control"
	for project in ("project_one", "project_two"):
		(runs / project).mkdir()
		(runs / project / "state.json").write_text(json.dumps({"id": project, "tenant_id": "tenant_one"}))
		service = MissionService(JsonMissionRepository(control)); service.bootstrap("tenant_one", project, "owner", {"title": "x"})
	monkeypatch.setenv("SIMULACRA_RUNS_DIR", str(runs))
	assert {(tenant, project) for _, tenant, project in deploy_process._discovered_mission_workers()} == {("tenant_one", "project_one"), ("tenant_one", "project_two")}
	assert {(tenant, project) for _, tenant, project in deploy_process._discovered_mission_workers(project_only="project_one")} == {("tenant_one", "project_one")}
	state = control / "tenant_one" / "project_two" / "missions" / "discovery.json"
	state.write_text('{"schema_version":1,"mission_id":"mission_x","tenant_id":"wrong","project_id":"project_two"}')
	assert {(tenant, project) for _, tenant, project in deploy_process._discovered_mission_workers()} == {("tenant_one", "project_one")}
	state.unlink(); state.symlink_to(control / "tenant_one" / "project_one" / "missions" / "discovery.json")
	assert {(tenant, project) for _, tenant, project in deploy_process._discovered_mission_workers()} == {("tenant_one", "project_one")}
	workspace_state = runs / "project_one" / "state.json"
	valid = json.dumps({"id": "project_one", "tenant_id": "tenant_one"})
	workspace_state.unlink()
	assert deploy_process._discovered_mission_workers() == []
	workspace_state.symlink_to(runs / "project_two" / "state.json")
	assert deploy_process._discovered_mission_workers() == []
	workspace_state.unlink()
	for payload in ("{", "[]", json.dumps({"id": "wrong", "tenant_id": "tenant_one"}), json.dumps({"id": "project_one", "tenant_id": "wrong"})):
		workspace_state.write_text(payload)
		assert deploy_process._discovered_mission_workers() == []
	workspace_state.write_text(valid)
	assert {(tenant, project) for _, tenant, project in deploy_process._discovered_mission_workers()} == {("tenant_one", "project_one")}


def test_mission_discovery_index_survives_evidence_state_beyond_read_cap(monkeypatch, tmp_path):
	runs = tmp_path / "runs"; runs.mkdir(); control = runs / ".mission-control"
	workspace = runs / "project_large"; workspace.mkdir()
	(workspace / "state.json").write_text(json.dumps({"id": "project_large", "tenant_id": "tenant_one"}))
	service = MissionService(JsonMissionRepository(control))
	service.bootstrap("tenant_one", "project_large", "owner", {"title": "large evidence"})
	trigger = service.add_trigger("tenant_one", "project_large", {"type": "cron", "cron": "*/5 * * * *"})
	service.repository.mutate(
		"tenant_one", "project_large",
		lambda records: records["triggers"][trigger.id].update({"next_due_at": "2020-01-01T00:00:00+00:00"}),
	)
	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_one"; graph["metadata"]["project_id"] = "project_large"
	store = OperationGraphStore(workspace, tenant_id="tenant_one", project_id="project_large")
	revision = store.create_revision(graph, expected_revision_hash=None)
	store.approve_revision(revision.revision_hash, actor_id="owner")
	service.repository.mutate(
		"tenant_one", "project_large",
		lambda records: records["deliverables"].update({
			"preserved_evidence": {"validation_evidence": [{"evidence": "x" * (deploy_process._MAX_DISCOVERY_STATE_BYTES + 1)}]},
		}),
	)
	directory = control / "tenant_one" / "project_large" / "missions"
	assert (directory / "state.json").stat().st_size > deploy_process._MAX_DISCOVERY_STATE_BYTES
	assert (directory / "discovery.json").stat().st_size < 1024
	monkeypatch.setenv("SIMULACRA_RUNS_DIR", str(runs))
	discovered = deploy_process._discovered_mission_workers()
	assert [(tenant, project) for _, tenant, project in discovered] == [("tenant_one", "project_large")]
	worker, tenant, project = discovered[0]
	# Production discovery wires the durable assignment admission boundary into
	# the actual Mission worker, before it considers work for this project.
	assert worker.coordinator is not None
	calls: list[tuple[str, str]] = []
	original_recover = worker.coordinator.recover_project
	def observed_recover(tenant_id: str, project_id: str):
		calls.append((tenant_id, project_id)); return original_recover(tenant_id, project_id)
	worker.coordinator.recover_project = observed_recover
	assert worker.consume(tenant, project) is None
	assert calls == [(tenant, project)]
	assert worker.service.mission(tenant, project).title == "large evidence"
	assert len(worker.schedule_due_cron(tenant, project)) == 1


def test_discovered_mission_worker_recovers_real_incomplete_assignment_before_claim(monkeypatch, tmp_path):
	runs = tmp_path / "runs"; runs.mkdir(); tenant = "tenant_one"; project = "project_one"
	workspace = runs / project; workspace.mkdir(); (workspace / "state.json").write_text(json.dumps({"id": project, "tenant_id": tenant}))
	control = runs / ".mission-control"; mission = MissionService(JsonMissionRepository(control))
	mission.bootstrap(tenant, project, "owner", {"title": "assignment"})
	agent = mission.add_agent(tenant, project, {"name": "Agent", "role": "builder", "mandate": "build"})
	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"].update({"tenant_id": tenant, "project_id": project})
	store = OperationGraphStore(workspace, tenant_id=tenant, project_id=project)
	revision = store.create_revision(graph, expected_revision_hash=None); store.approve_revision(revision.revision_hash, actor_id="owner")
	collaboration = JsonCollaborationRepository(runs / ".cmul8-control")
	CollaborationService(collaboration).create_room(tenant_id=tenant, project_id=project, creator_id="owner")
	coordinator = AssignmentCoordinator(collaboration, mission, workspace, runs_root=runs, clock=lambda: "2026-01-02T09:00:00Z")
	coordinator.fault_injector = lambda stage: (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_queued_before_COMPLETE" else None
	with pytest.raises(RuntimeError, match="after_queued_before_COMPLETE"):
		coordinator.assign(tenant_id=tenant, project_id=project, authenticated_human_actor_id="owner", client_request_id="request_1", body="assign", title="task", objective="deliver", acceptance_criteria=["verified"], assigned_agent_ids=[agent.id], graph_revision=revision.revision_hash)
	journal = next(runs.glob(f".workplace-control/{tenant}/{project}/assignment-transactions/*/conversation_assignment/*.json"))
	assert json.loads(journal.read_text())["state"] != "COMPLETE"
	assert mission.claim_next(tenant, project, "worker") is None
	monkeypatch.setenv("SIMULACRA_RUNS_DIR", str(runs))
	worker, discovered_tenant, discovered_project = deploy_process._discovered_mission_workers()[0]
	seen: list[str] = []; original_claim = worker.service.claim_next
	def observed_claim(*args, **kwargs):
		seen.append(json.loads(journal.read_text())["state"])
		assert seen[-1] == "COMPLETE"
		return None
	worker.service.claim_next = observed_claim
	assert worker.consume(discovered_tenant, discovered_project) is None
	row = json.loads(journal.read_text()); assert seen == ["COMPLETE"] and row["state"] == "COMPLETE"
	assert worker.coordinator.visible_result(tenant_id=tenant, project_id=project, transaction_id=row["transaction_id"]) is not None
	with worker.coordinator.project_claim_guard(tenant, project) as admission:
		assert admission.allows(row["transaction_id"], row["reserved_run_id"])


def test_mission_discovery_explicit_tenant_filter_excludes_same_project_other_tenant(monkeypatch, tmp_path):
	runs = tmp_path / "runs"; runs.mkdir(); control = runs / ".mission-control"; project = "project_shared"
	(runs / project).mkdir()
	for tenant in ("tenant_one", "tenant_two"):
		MissionService(JsonMissionRepository(control)).bootstrap(tenant, project, "owner", {"title": tenant})
	# This workspace is self-consistent with tenant_two; an explicit tenant_one
	# worker must never execute it merely because the project id matches.
	(runs / project / "state.json").write_text(json.dumps({"id": project, "tenant_id": "tenant_two"}))
	monkeypatch.setenv("SIMULACRA_RUNS_DIR", str(runs))
	assert {(tenant, item) for _, tenant, item in deploy_process._discovered_mission_workers()} == {("tenant_two", project)}
	assert deploy_process._discovered_mission_workers(project_only=project, tenant_only="tenant_one") == []
	assert {(tenant, item) for _, tenant, item in deploy_process._discovered_mission_workers(project_only=project, tenant_only="tenant_two")} == {("tenant_two", project)}
