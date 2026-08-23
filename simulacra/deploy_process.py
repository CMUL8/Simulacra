"""Executable OCI process contract for CMUL8 Cloud and private Docker Compose."""

from __future__ import annotations

import argparse
import os
import signal
import socket
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

WORKER_SOCKET = Path(os.environ.get("CMUL8_WORKER_SOCKET", "/tmp/cmul8-worker.sock"))


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


def worker() -> int:
	WORKER_SOCKET.unlink(missing_ok=True)
	server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
	server.bind(str(WORKER_SOCKET))
	WORKER_SOCKET.chmod(0o600)
	server.listen(8)
	server.settimeout(1)
	running = True
	def stop(_signum: int, _frame: object) -> None:
		nonlocal running
		running = False
	signal.signal(signal.SIGTERM, stop)
	signal.signal(signal.SIGINT, stop)
	try:
		while running:
			try:
				connection, _ = server.accept()
			except TimeoutError:
				continue
			with connection:
				request = connection.recv(32).decode("ascii", "replace").strip()
				ready = request == "LIVE" or (request == "READY" and _queue_reachable())
				connection.sendall(b"OK\n" if ready else b"NOT_READY\n")
	finally:
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
