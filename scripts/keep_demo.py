#!/usr/bin/env python3
"""Keep API (:8000) and console (:5173) alive. Daemonized; safe to leave running."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIDFILE = Path("/tmp/simulacra-keep.pid")
API_LOG = Path("/tmp/simulacra-api.log")
CONSOLE_LOG = Path("/tmp/simulacra-console.log")
KEEP_LOG = Path("/tmp/simulacra-keep.log")


def log(msg: str) -> None:
	line = f"{time.strftime('%H:%M:%S')} {msg}"
	print(line, flush=True)
	with KEEP_LOG.open("a") as f:
		f.write(line + "\n")


def up(url: str) -> bool:
	try:
		with urllib.request.urlopen(url, timeout=2) as res:
			return 200 <= res.status < 500
	except Exception:
		return False


def kill_port(port: int) -> None:
	subprocess.run(
		f"lsof -ti:{port} | xargs kill -9 2>/dev/null || true",
		shell=True,
		check=False,
	)


def start_api(env: dict[str, str]) -> subprocess.Popen:
	kill_port(8000)
	time.sleep(0.4)
	API_LOG.write_text("")
	return subprocess.Popen(
		[
			str(ROOT / ".venv" / "bin" / "uvicorn"),
			"apps.api.main:app",
			"--host",
			"127.0.0.1",
			"--port",
			"8000",
		],
		cwd=ROOT,
		env=env,
		stdout=API_LOG.open("a"),
		stderr=subprocess.STDOUT,
		start_new_session=True,
	)


def start_console(env: dict[str, str]) -> subprocess.Popen:
	kill_port(5173)
	time.sleep(0.4)
	CONSOLE_LOG.write_text("")
	npm = subprocess.run(["which", "npm"], capture_output=True, text=True, check=False).stdout.strip() or "npm"
	return subprocess.Popen(
		[npm, "run", "dev"],
		cwd=ROOT / "apps" / "console",
		env=env,
		stdout=CONSOLE_LOG.open("a"),
		stderr=subprocess.STDOUT,
		start_new_session=True,
	)


def load_env() -> dict[str, str]:
	env = os.environ.copy()
	dotenv = ROOT / ".env"
	if dotenv.exists():
		for line in dotenv.read_text().splitlines():
			line = line.strip()
			if not line or line.startswith("#") or "=" not in line:
				continue
			k, _, v = line.partition("=")
			env[k.strip()] = v.strip().strip('"').strip("'")
	env["SIMULACRA_USE_PRIME"] = env.get("SIMULACRA_USE_PRIME") or "1"
	env.setdefault("SIMULACRA_SANDBOX", "auto")
	# Activate venv path
	venv_bin = str(ROOT / ".venv" / "bin")
	env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
	env["VIRTUAL_ENV"] = str(ROOT / ".venv")
	return env


def already_running() -> bool:
	if not PIDFILE.exists():
		return False
	try:
		pid = int(PIDFILE.read_text().strip())
		os.kill(pid, 0)
		return True
	except Exception:
		return False


def main() -> int:
	if already_running() and "--force" not in sys.argv:
		print(f"keep-demo already running (pid {PIDFILE.read_text().strip()})")
		return 0

	# Daemonize
	if os.fork() > 0:
		# parent waits briefly for readiness then exits
		for _ in range(40):
			if up("http://127.0.0.1:8000/health") and up("http://127.0.0.1:5173/"):
				print(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2).read().decode())
				print("Console: http://localhost:5173")
				print(f"Supervisor: {KEEP_LOG}")
				return 0
			time.sleep(0.5)
		print("Started supervisor but services not ready yet — check", KEEP_LOG, file=sys.stderr)
		return 0

	os.setsid()
	if os.fork() > 0:
		os._exit(0)

	sys.stdout.flush()
	sys.stderr.flush()
	devnull = os.open(os.devnull, os.O_RDWR)
	os.dup2(devnull, 0)
	# keep writing to keep log via log()
	PIDFILE.write_text(str(os.getpid()))

	def cleanup(*_args: object) -> None:
		PIDFILE.unlink(missing_ok=True)
		os._exit(0)

	signal.signal(signal.SIGTERM, cleanup)
	signal.signal(signal.SIGINT, cleanup)

	env = load_env()
	log(f"supervisor start pid={os.getpid()} prime={env.get('SIMULACRA_USE_PRIME')} sandbox={env.get('SIMULACRA_SANDBOX')}")
	api = start_api(env)
	console = start_console(env)

	while True:
		try:
			if not up("http://127.0.0.1:8000/health"):
				log("API down — restarting")
				try:
					api.kill()
				except Exception:
					pass
				api = start_api(env)
				time.sleep(2)
			if not up("http://127.0.0.1:5173/"):
				log("Console down — restarting")
				try:
					console.kill()
				except Exception:
					pass
				console = start_console(env)
				time.sleep(2)
		except Exception as exc:  # noqa: BLE001
			log(f"watch error: {exc}")
		time.sleep(5)


if __name__ == "__main__":
	raise SystemExit(main())
