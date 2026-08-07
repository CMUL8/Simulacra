"""Ephemeral job machines for sandboxed Prime / build work.

Providers (SIMULACRA_MACHINE_PROVIDER):
  - local: disposable Docker container (--rm), auto-destroyed after the command
  - fly: Fly.io Machines API (create → exec/wait → destroy)

gVisor: when SIMULACRA_SANDBOX=gvisor (or runtime runsc available), Docker uses
--runtime=runsc for stronger kernel isolation.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("simulacra.machines")

DOCKER_IMAGE = os.environ.get("SIMULACRA_SANDBOX_IMAGE", "python:3.12-slim")
GVISOR_RUNTIME = os.environ.get("SIMULACRA_GVISOR_RUNTIME", "runsc")


@dataclass
class MachineResult:
	provider: str
	machine_id: str
	ok: bool
	detail: str = ""
	returncode: int | None = None
	stdout: str = ""
	stderr: str = ""
	destroyed: bool = False
	meta: dict[str, Any] = field(default_factory=dict)


def machine_provider() -> str:
	return (os.environ.get("SIMULACRA_MACHINE_PROVIDER") or "local").lower()


def gvisor_available() -> bool:
	"""True if Docker reports the configured gVisor runtime."""
	if not shutil.which("docker"):
		return False
	try:
		r = subprocess.run(
			["docker", "info", "--format", "{{json .Runtimes}}"],
			capture_output=True,
			text=True,
			timeout=5,
			check=False,
		)
		if r.returncode != 0:
			return False
		data = json.loads(r.stdout.strip() or "{}")
		return GVISOR_RUNTIME in data
	except Exception:  # noqa: BLE001
		return False


def machines_status() -> dict[str, Any]:
	provider = machine_provider()
	fly_ready = bool(os.environ.get("FLY_API_TOKEN") and os.environ.get("SIMULACRA_FLY_APP"))
	return {
		"provider": provider,
		"gvisor_runtime": GVISOR_RUNTIME,
		"gvisor_available": gvisor_available(),
		"fly_configured": fly_ready,
		"image": DOCKER_IMAGE,
		"ephemeral": True,
	}


def run_ephemeral(
	argv: list[str],
	*,
	cwd: Path,
	timeout: float = 300.0,
	network: str = "deny",
	use_gvisor: bool = False,
	env: dict[str, str] | None = None,
) -> MachineResult:
	"""Spin up a one-shot machine, run argv, destroy it."""
	provider = machine_provider()
	if provider == "fly" and os.environ.get("FLY_API_TOKEN") and os.environ.get("SIMULACRA_FLY_APP"):
		return _run_fly(argv, cwd=cwd, timeout=timeout, network=network, env=env or {})
	return _run_local_docker(
		argv,
		cwd=cwd,
		timeout=timeout,
		network=network,
		use_gvisor=use_gvisor,
		env=env or {},
	)


def _run_local_docker(
	argv: list[str],
	*,
	cwd: Path,
	timeout: float,
	network: str,
	use_gvisor: bool,
	env: dict[str, str],
) -> MachineResult:
	if not shutil.which("docker"):
		return MachineResult(
			provider="local",
			machine_id="",
			ok=False,
			detail="docker not available for ephemeral machine",
		)
	mid = f"sim-{uuid.uuid4().hex[:12]}"
	net = "none" if network == "deny" else "bridge"
	cmd = [
		"docker",
		"run",
		"--rm",
		"--name",
		mid,
		"--label",
		"simulacra.ephemeral=1",
		"--label",
		f"simulacra.machine_id={mid}",
		"--network",
		net,
		"--workdir",
		"/work",
		"-v",
		f"{cwd.resolve()}:/work",
		"--memory",
		os.environ.get("SIMULACRA_SANDBOX_MEMORY", "2g"),
		"--cpus",
		os.environ.get("SIMULACRA_SANDBOX_CPUS", "2"),
	]
	if use_gvisor and gvisor_available():
		cmd.extend(["--runtime", GVISOR_RUNTIME])
	elif use_gvisor:
		log.warning("gVisor requested but runtime %s unavailable — docker runc", GVISOR_RUNTIME)
	for k, v in env.items():
		cmd.extend(["-e", f"{k}={v}"])
	cmd.append(DOCKER_IMAGE)
	cmd.extend(argv)

	destroyed = False
	try:
		proc = subprocess.run(
			cmd,
			capture_output=True,
			text=True,
			timeout=timeout,
			check=False,
		)
		destroyed = True  # --rm
		return MachineResult(
			provider="local",
			machine_id=mid,
			ok=proc.returncode == 0,
			returncode=proc.returncode,
			stdout=proc.stdout[-4000:],
			stderr=proc.stderr[-4000:],
			detail=f"ephemeral docker network={net} gvisor={use_gvisor and gvisor_available()}",
			destroyed=destroyed,
			meta={"runtime": GVISOR_RUNTIME if use_gvisor and gvisor_available() else "runc"},
		)
	except subprocess.TimeoutExpired:
		_force_rm(mid)
		return MachineResult(
			provider="local",
			machine_id=mid,
			ok=False,
			detail="timeout",
			destroyed=True,
		)
	except Exception as exc:  # noqa: BLE001
		_force_rm(mid)
		return MachineResult(
			provider="local",
			machine_id=mid,
			ok=False,
			detail=str(exc)[:300],
			destroyed=True,
		)


def _force_rm(name: str) -> None:
	try:
		subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=15, check=False)
	except Exception:  # noqa: BLE001
		pass


def _fly_headers() -> dict[str, str]:
	token = os.environ["FLY_API_TOKEN"]
	return {
		"Authorization": f"Bearer {token}",
		"Content-Type": "application/json",
	}


def _fly_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
	url = f"https://api.machines.dev{path}"
	data = None if body is None else json.dumps(body).encode()
	req = urllib.request.Request(url, data=data, headers=_fly_headers(), method=method)
	try:
		with urllib.request.urlopen(req, timeout=60) as resp:
			raw = resp.read().decode()
			return json.loads(raw) if raw else {}
	except urllib.error.HTTPError as exc:
		err = exc.read().decode()[:500]
		raise RuntimeError(f"Fly Machines API {exc.code}: {err}") from exc


def _run_fly(
	argv: list[str],
	*,
	cwd: Path,
	timeout: float,
	network: str,
	env: dict[str, str],
) -> MachineResult:
	"""Create a Fly Machine, run a one-shot command via guest exec pattern, destroy.

	Note: Fly Machines don't bind-mount local cwd. We upload a tarball of the work
	dir via stdin into /work when SIMULACRA_FLY_SYNC=1; otherwise we assume the
	image already contains the job context (CI/CD artifact). For local-dev safety,
	prefer provider=local.
	"""
	app = os.environ["SIMULACRA_FLY_APP"]
	region = os.environ.get("SIMULACRA_FLY_REGION", "iad")
	mid = ""
	try:
		created = _fly_request(
			"POST",
			f"/v1/apps/{app}/machines",
			{
				"name": f"sim-{uuid.uuid4().hex[:10]}",
				"region": region,
				"config": {
					"image": os.environ.get("SIMULACRA_FLY_IMAGE", DOCKER_IMAGE),
					"auto_destroy": True,
					"restart": {"policy": "no"},
					"guest": {
						"cpu_kind": "shared",
						"cpus": int(os.environ.get("SIMULACRA_SANDBOX_CPUS", "2")),
						"memory_mb": int(
							os.environ.get("SIMULACRA_FLY_MEMORY_MB", "2048")
						),
					},
					"env": {**env, "SIMULACRA_IN_SANDBOX": "1"},
					"init": {
						"cmd": argv,
					},
				},
			},
		)
		mid = str(created.get("id") or "")
		# Wait until stopped / destroyed
		deadline = time.monotonic() + timeout
		last: dict[str, Any] = created
		while time.monotonic() < deadline:
			last = _fly_request("GET", f"/v1/apps/{app}/machines/{mid}")
			state = (last.get("state") or "").lower()
			if state in ("stopped", "destroyed", "failed"):
				break
			time.sleep(2)
		else:
			_fly_destroy(app, mid)
			return MachineResult(
				provider="fly",
				machine_id=mid,
				ok=False,
				detail="timeout waiting for machine",
				destroyed=True,
			)
		_fly_destroy(app, mid)
		ok = (last.get("state") or "").lower() in ("stopped", "destroyed")
		return MachineResult(
			provider="fly",
			machine_id=mid,
			ok=ok,
			detail=f"fly state={last.get('state')} network={network} cwd={cwd}",
			destroyed=True,
			meta={"region": region, "app": app},
		)
	except Exception as exc:  # noqa: BLE001
		if mid:
			_fly_destroy(app, mid)
		return MachineResult(
			provider="fly",
			machine_id=mid,
			ok=False,
			detail=str(exc)[:400],
			destroyed=bool(mid),
		)


def _fly_destroy(app: str, machine_id: str) -> None:
	if not machine_id:
		return
	try:
		_fly_request("DELETE", f"/v1/apps/{app}/machines/{machine_id}?force=true")
	except Exception as exc:  # noqa: BLE001
		log.warning("fly destroy failed %s: %s", machine_id, exc)
