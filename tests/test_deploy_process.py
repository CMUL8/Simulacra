from __future__ import annotations

import socket
import threading
import os

from simulacra import deploy_process
from apps.api.main import liveness, readiness


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


def test_worker_health_probes_a_running_worker_socket(monkeypatch, tmp_path):
	socket_path = deploy_process.Path(f"/tmp/cm8-health-{os.getpid()}.sock")
	socket_path.unlink(missing_ok=True)
	monkeypatch.setattr(deploy_process, "WORKER_SOCKET", socket_path)
	server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	server.bind(str(socket_path))
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


def test_image_defines_the_process_entrypoint():
	dockerfile = (deploy_process.Path(__file__).parents[1] / "Dockerfile").read_text()
	assert 'ENTRYPOINT ["/opt/cmul8/bin/cmul8-entrypoint"]' in dockerfile
	assert 'CMD ["api"]' in dockerfile
	assert "worker-health" in dockerfile


def test_api_exposes_container_health_contract():
	assert liveness()["status"] == "live"
	assert readiness()["status"] == "ready"
