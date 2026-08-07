"""Execution sandbox for Prime / build work.

Modes (SIMULACRA_SANDBOX or tenant policy):
  - docker: run commands in a disposable container (network none, bind-mount run dir)
  - worktree: hardened local jail (scrubbed env, cwd jail, optional macOS sandbox-exec)
  - auto: docker if daemon available else worktree
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("simulacra.sandbox")

DOCKER_IMAGE = os.environ.get("SIMULACRA_SANDBOX_IMAGE", "python:3.12-slim")


@dataclass
class SandboxResult:
	mode: str
	ok: bool
	detail: str = ""
	returncode: int | None = None
	stdout: str = ""
	stderr: str = ""


def docker_available() -> bool:
	if not shutil.which("docker"):
		return False
	try:
		r = subprocess.run(
			["docker", "info"],
			capture_output=True,
			timeout=5,
			check=False,
		)
		return r.returncode == 0
	except Exception:  # noqa: BLE001
		return False


def resolve_mode(requested: str | None = None) -> str:
	mode = (requested or os.environ.get("SIMULACRA_SANDBOX") or "auto").lower()
	if mode == "auto":
		return "docker" if docker_available() else "worktree"
	if mode == "docker" and not docker_available():
		log.warning("docker requested but unavailable — falling back to worktree")
		return "worktree"
	return mode


def sandbox_status() -> dict[str, Any]:
	mode = resolve_mode()
	return {
		"requested": os.environ.get("SIMULACRA_SANDBOX", "auto"),
		"active": mode,
		"docker_available": docker_available(),
		"image": DOCKER_IMAGE if mode == "docker" else None,
		"network": "deny",
		"trust_model": (
			"container isolation"
			if mode == "docker"
			else "worktree jail + scrubbed env (not a full security boundary)"
		),
	}


def _scrubbed_env() -> dict[str, str]:
	"""Pass through only what Prime/build needs — no ambient host secrets beyond configured keys."""
	allow = {
		"PATH",
		"HOME",
		"USER",
		"LANG",
		"LC_ALL",
		"TERM",
		"OPENROUTER_API_KEY",
		"ANTHROPIC_API_KEY",
		"OPENAI_API_KEY",
		"SIMULACRA_USE_PRIME",
		"SIMULACRA_PRIME_PROVIDER",
		"SIMULACRA_PRIME_MODEL",
		"SIMULACRA_SANDBOX",
		"PRIME_AGENT_BIN",
		"NODE_PATH",
		"npm_config_cache",
	}
	env = {k: v for k, v in os.environ.items() if k in allow}
	env["SIMULACRA_IN_SANDBOX"] = "1"
	env["HOME"] = env.get("HOME") or "/tmp"
	return env


def _macos_sandbox_profile(work_dir: Path) -> str | None:
	"""Seatbelt profile: allow writes only under work_dir."""
	if os.uname().sysname != "Darwin":
		return None
	root = str(work_dir.resolve())
	return f"""
(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow sysctl-read)
(allow mach-lookup)
(allow file-read*)
(allow file-write* (subpath "{root}"))
(allow file-write* (subpath "/tmp"))
(allow file-write* (subpath "/private/tmp"))
(allow network* (remote tcp "localhost:*"))
"""


def run_sandboxed(
	argv: list[str],
	*,
	cwd: Path,
	mode: str | None = None,
	timeout: float = 300.0,
	network: str = "deny",
) -> SandboxResult:
	"""Run argv inside the resolved sandbox. cwd is the only writable project tree."""
	active = resolve_mode(mode)
	cwd = cwd.resolve()
	cwd.mkdir(parents=True, exist_ok=True)

	if active == "docker":
		return _run_docker(argv, cwd=cwd, timeout=timeout, network=network)
	return _run_worktree(argv, cwd=cwd, timeout=timeout)


def _run_docker(
	argv: list[str],
	*,
	cwd: Path,
	timeout: float,
	network: str,
) -> SandboxResult:
	net = "none" if network == "deny" else "bridge"
	cmd = [
		"docker",
		"run",
		"--rm",
		"--network",
		net,
		"--workdir",
		"/work",
		"-v",
		f"{cwd}:/work",
		"--memory",
		os.environ.get("SIMULACRA_SANDBOX_MEMORY", "2g"),
		"--cpus",
		os.environ.get("SIMULACRA_SANDBOX_CPUS", "2"),
		DOCKER_IMAGE,
		*argv,
	]
	try:
		proc = subprocess.run(
			cmd,
			capture_output=True,
			text=True,
			timeout=timeout,
			check=False,
			env=_scrubbed_env(),
		)
		return SandboxResult(
			mode="docker",
			ok=proc.returncode == 0,
			returncode=proc.returncode,
			stdout=proc.stdout[-4000:],
			stderr=proc.stderr[-4000:],
			detail=f"docker network={net}",
		)
	except subprocess.TimeoutExpired:
		return SandboxResult(mode="docker", ok=False, detail="timeout")
	except Exception as exc:  # noqa: BLE001
		return SandboxResult(mode="docker", ok=False, detail=str(exc)[:300])


def _run_worktree(argv: list[str], *, cwd: Path, timeout: float) -> SandboxResult:
	env = _scrubbed_env()
	cmd = list(argv)
	profile = _macos_sandbox_profile(cwd)
	profile_path = None
	if profile and shutil.which("sandbox-exec"):
		profile_path = cwd / "work" / "sandbox.sb"
		profile_path.parent.mkdir(parents=True, exist_ok=True)
		profile_path.write_text(profile)
		cmd = ["sandbox-exec", "-f", str(profile_path), *argv]
	try:
		proc = subprocess.run(
			cmd,
			cwd=cwd,
			capture_output=True,
			text=True,
			timeout=timeout,
			check=False,
			env=env,
		)
		detail = "worktree+seatbelt" if profile_path else "worktree"
		return SandboxResult(
			mode="worktree",
			ok=proc.returncode == 0,
			returncode=proc.returncode,
			stdout=proc.stdout[-4000:],
			stderr=proc.stderr[-4000:],
			detail=detail,
		)
	except subprocess.TimeoutExpired:
		return SandboxResult(mode="worktree", ok=False, detail="timeout")
	except Exception as exc:  # noqa: BLE001
		return SandboxResult(mode="worktree", ok=False, detail=str(exc)[:300])


def prepare_project_sandbox(project_id: str, *, tenant_sandbox: str | None = None) -> dict[str, Any]:
	"""Record sandbox decision into audit pack; returns status for UI."""
	from .runs import project_dir

	status = sandbox_status()
	if tenant_sandbox:
		status["requested"] = tenant_sandbox
		status["active"] = resolve_mode(tenant_sandbox)
	root = project_dir(project_id)
	audit = root / "audit"
	audit.mkdir(parents=True, exist_ok=True)
	import json

	(audit / "sandbox.json").write_text(json.dumps(status, indent=2))
	# Ensure jail dirs exist
	for sub in ("work", "outputs", "app", "inputs"):
		(root / sub).mkdir(parents=True, exist_ok=True)
	return status
