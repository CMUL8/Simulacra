"""Bounded, cancellable jobs — one builder per project; admission for multi-tenant scale."""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .events import emit_event
from .runs import load_state, save_state

log = logging.getLogger("simulacra.jobs")

# Host-wide cap (one Railway/uvicorn process). Override with SIMULACRA_MAX_RUNNING_JOBS.
MAX_RUNNING_JOBS = int(os.environ.get("SIMULACRA_MAX_RUNNING_JOBS", "48"))

# APP_MAKER_CONTRACT + PRODUCT_SPEC §3A.6 defaults
BOUNDS: dict[str, dict[str, float | int]] = {
	"bootstrap": {"timeout": 480, "max_steps": 50, "stall": 120},
	# Chat may chain into iterate; long LLM waits without tools are normal.
	"agent_chat": {"timeout": 900, "max_steps": 120, "stall": 300},
	"plan_ask": {"timeout": 900, "max_steps": 120, "stall": 300},  # alias bounds
	"build_run": {"timeout": 420, "max_steps": 50, "stall": 90},
	# Heavy visual rewrites (charts/timeline/comparisons) need room past CSS.
	"iterate_run": {"timeout": 420, "max_steps": 50, "stall": 90},
	"iterate_ask": {"timeout": 90, "max_steps": 6, "stall": 45},
	"reingest": {"timeout": 180, "max_steps": 20, "stall": 60},
}


class JobConflictError(RuntimeError):
	pass


class JobCancelled(RuntimeError):
	pass


@dataclass
class JobRecord:
	id: str
	project_id: str
	kind: str
	status: str = "running"
	started_at: float = field(default_factory=time.monotonic)
	deadline: float = 0.0
	steps: int = 0
	max_steps: int = 40
	stall_secs: float = 45.0
	last_event_at: float = field(default_factory=time.monotonic)
	cancel_requested: bool = False
	error: str | None = None
	label: str = ""
	tenant_id: str = ""
	tool_signatures: dict[str, int] = field(default_factory=dict)
	result: Any = None
	thread: threading.Thread | None = None
	persist_agent_metadata: bool = True


_lock = threading.Lock()
_jobs: dict[str, JobRecord] = {}  # project_id -> active job


def _running_jobs() -> list[JobRecord]:
	return [j for j in _jobs.values() if j.status == "running"]


def _admit(project_id: str, *, tenant_id: str) -> None:
	"""Raise JobConflictError if host or tenant is at capacity. Call under _lock."""
	running = _running_jobs()
	if len(running) >= MAX_RUNNING_JOBS:
		raise JobConflictError(
			f"Host busy ({len(running)}/{MAX_RUNNING_JOBS} jobs) — try again in a moment"
		)
	if tenant_id:
		try:
			from .tenants import get_tenant

			cap = int(get_tenant(tenant_id).policy.max_concurrent_jobs or 2)
		except Exception:  # noqa: BLE001
			cap = 2
		tenant_n = sum(1 for j in running if j.tenant_id == tenant_id)
		if tenant_n >= cap:
			raise JobConflictError(
				f"Workspace limit reached ({tenant_n}/{cap} concurrent jobs). "
				"Wait for one to finish, or stop a running job."
			)


def get_job(project_id: str) -> JobRecord | None:
	with _lock:
		return _jobs.get(project_id)


def job_snapshot(project_id: str) -> dict[str, Any]:
	job = get_job(project_id)
	if job is None:
		try:
			return load_state(project_id).job
		except FileNotFoundError:
			return {"id": None, "status": "idle"}
	return {
		"id": job.id,
		"kind": job.kind,
		"status": job.status,
		"steps": job.steps,
		"max_steps": job.max_steps,
		"cancel_requested": job.cancel_requested,
		"error": job.error,
		"label": job.label,
		"started_at": datetime.fromtimestamp(job.started_at, tz=UTC).isoformat()
		if False
		else datetime.now(UTC).isoformat(),
	}


def _persist(job: JobRecord, *, extra_prime: dict[str, Any] | None = None) -> None:
	try:
		state = load_state(job.project_id)
	except FileNotFoundError:
		return
	deadline_iso = None
	if job.deadline:
		# approximate wall deadline for clients
		remaining = max(0.0, job.deadline - time.monotonic())
		deadline_iso = (datetime.now(UTC) + timedelta(seconds=remaining)).isoformat()
	state.job = {
		"id": job.id,
		"kind": job.kind,
		"status": job.status,
		"started_at": datetime.now(UTC).isoformat(),
		"deadline_at": deadline_iso,
		"steps": job.steps,
		"max_steps": job.max_steps,
		"cancel_requested": job.cancel_requested,
		"error": job.error,
		"label": job.label,
	}
	if extra_prime and job.persist_agent_metadata:
		state.prime = {**state.prime, **extra_prime}
	save_state(state)


def note_event(project_id: str, *, tool_sig: str | None = None) -> None:
	job = get_job(project_id)
	if not job or job.status != "running":
		return
	job.last_event_at = time.monotonic()
	job.steps += 1
	if tool_sig:
		job.tool_signatures[tool_sig] = job.tool_signatures.get(tool_sig, 0) + 1
	_persist(job)


def check_bounds(project_id: str) -> None:
	"""Raise JobCancelled if timeout/steps/stall/cancel/repeat-tool tripped."""
	job = get_job(project_id)
	if not job or job.status != "running":
		return
	if job.cancel_requested:
		raise JobCancelled("cancelled by user")
	now = time.monotonic()
	if now > job.deadline:
		raise JobCancelled("timeout")
	if job.steps >= job.max_steps:
		raise JobCancelled("max_steps")
	# Pure chat turns often think for minutes with no tool events — do not stall-kill them.
	# Build/iterate still use stall so runaway tool loops die.
	if job.kind not in ("agent_chat", "plan_ask") and now - job.last_event_at > job.stall_secs:
		raise JobCancelled("stall")
	# Writes to the same path are normal (App.tsx often takes many passes).
	# Only trip on identical non-write spam (e.g. stuck read/search loops).
	for sig, count in job.tool_signatures.items():
		limit = 12 if sig.lower().startswith(("write:", "edit:", "apply_patch:", "str_replace:")) else 5
		if count >= limit:
			raise JobCancelled(f"repeated_tool:{sig}")


def request_cancel(project_id: str) -> dict[str, Any]:
	"""Request cancel of a live job. Idempotent when nothing is running."""
	job = get_job(project_id)
	if not job or job.status not in ("running", "settling"):
		# Heal persisted ghost "running" so clients unlock
		try:
			state = load_state(project_id)
			job_state = dict(state.job or {})
			if job_state.get("status") in ("running", "settling"):
				job_state["status"] = "idle"
				job_state["cancel_requested"] = False
				state.job = job_state
				save_state(state)
		except FileNotFoundError:
			pass
		return {"ok": True, "already_idle": True, "error": None}
	job.cancel_requested = True
	emit_event(project_id, "phase", label="Stop requested", status="running")
	_persist(job)
	# Best-effort abort hook
	abort = _abort_hooks.get(project_id)
	if abort:
		try:
			abort()
		except Exception as exc:  # noqa: BLE001
			log.warning("abort hook failed: %s", exc)
	return {"ok": True, "already_idle": False, "job_id": job.id}



_abort_hooks: dict[str, Callable[[], None]] = {}


def register_abort(project_id: str, hook: Callable[[], None]) -> None:
	_abort_hooks[project_id] = hook


def clear_abort(project_id: str) -> None:
	_abort_hooks.pop(project_id, None)


def extend_job_budget(
	project_id: str,
	*,
	extra_secs: float = 420.0,
	extra_steps: int = 50,
) -> bool:
	"""Give a nested iterate/build more wall-clock and step room inside chat."""
	job = get_job(project_id)
	if not job or job.status != "running":
		return False
	job.deadline = max(job.deadline, time.monotonic() + float(extra_secs))
	job.max_steps = max(job.max_steps, job.steps + int(extra_steps))
	job.last_event_at = time.monotonic()
	_persist(job)
	return True


def start_job(
	project_id: str,
	kind: str,
	*,
	label: str,
	target: Callable[[JobRecord], Any],
	persist_agent_metadata: bool = True,
) -> JobRecord:
	bounds = BOUNDS.get(kind, BOUNDS["build_run"])
	try:
		tenant_id = str(load_state(project_id).tenant_id or "")
	except FileNotFoundError:
		tenant_id = ""

	with _lock:
		existing = _jobs.get(project_id)
		if existing and existing.status == "running":
			raise JobConflictError(f"Project already has running job {existing.id} ({existing.kind})")
		_admit(project_id, tenant_id=tenant_id)
		job = JobRecord(
			id=f"job_{uuid.uuid4().hex[:10]}",
			project_id=project_id,
			kind=kind,
			label=label,
			tenant_id=tenant_id,
			deadline=time.monotonic() + float(bounds["timeout"]),
			max_steps=int(bounds["max_steps"]),
			stall_secs=float(bounds["stall"]),
			persist_agent_metadata=persist_agent_metadata,
		)
		_jobs[project_id] = job

	emit_event(project_id, "phase", label=label, detail=kind, status="running", meta={"job_id": job.id})
	_persist(job, extra_prime={"status": "running"})

	def runner() -> None:
		t0 = time.monotonic()
		try:
			job.result = target(job)
			if job.cancel_requested:
				job.status = "cancelled"
				job.error = "cancelled"
			else:
				job.status = "settling"
				job.status = "idle"
			emit_event(
				project_id,
				"done" if job.status == "idle" else "phase",
				label="Job complete" if job.status == "idle" else "Job cancelled",
				detail=job.kind,
				status="done" if job.status == "idle" else "fail",
			)
		except JobCancelled as exc:
			job.status = "cancelled" if "cancel" in str(exc) else "failed"
			job.error = str(exc)
			emit_event(project_id, "error", label="Job stopped", detail=str(exc), status="fail")
		except Exception as exc:  # noqa: BLE001
			job.status = "failed"
			job.error = str(exc)[:400]
			log.exception("job %s failed", job.id)
			emit_event(project_id, "error", label="Job failed", detail=job.error, status="fail")
		finally:
			duration_ms = int((time.monotonic() - t0) * 1000)
			prime_status = {
				"cancelled": "cancelled",
				"failed": "error",
				"idle": "ok",
			}.get(job.status, job.status)
			# Do not overwrite pipeline-authored prime.source (heuristic vs prime)
			_persist(
				job,
				extra_prime={
					"status": prime_status,
					"steps": job.steps,
					"duration_ms": duration_ms,
					"last_error": job.error,
				},
			)
			# Keep terminal status visible briefly then mark idle in state
			if job.status in ("cancelled", "failed"):
				_persist(job)
			else:
				job.status = "idle"
				_persist(job)
			clear_abort(project_id)
			with _lock:
				if _jobs.get(project_id) is job:
					del _jobs[project_id]

	thread = threading.Thread(target=runner, name=f"simulacra-{job.id}", daemon=True)
	job.thread = thread
	thread.start()
	return job


def run_sync(
	project_id: str,
	kind: str,
	*,
	label: str,
	target: Callable[[JobRecord], Any],
) -> Any:
	"""Run a job on the current thread (for plan chat / short asks). Still bounds-checked."""
	bounds = BOUNDS.get(kind, BOUNDS["plan_ask"])
	with _lock:
		existing = _jobs.get(project_id)
		if existing and existing.status == "running":
			raise JobConflictError(f"Project already has running job {existing.id}")
		job = JobRecord(
			id=f"job_{uuid.uuid4().hex[:10]}",
			project_id=project_id,
			kind=kind,
			label=label,
			deadline=time.monotonic() + float(bounds["timeout"]),
			max_steps=int(bounds["max_steps"]),
			stall_secs=float(bounds["stall"]),
		)
		_jobs[project_id] = job
	_persist(job, extra_prime={"status": "running"})
	t0 = time.monotonic()
	try:
		check_bounds(project_id)
		result = target(job)
		job.result = result
		job.status = "idle"
		return result
	except JobCancelled as exc:
		job.status = "cancelled"
		job.error = str(exc)
		raise
	except Exception:
		job.status = "failed"
		raise
	finally:
		duration_ms = int((time.monotonic() - t0) * 1000)
		_persist(
			job,
			extra_prime={
				"status": "ok" if job.status == "idle" else job.status,
				"steps": job.steps,
				"duration_ms": duration_ms,
				"last_error": job.error,
			},
		)
		clear_abort(project_id)
		with _lock:
			if _jobs.get(project_id) is job:
				del _jobs[project_id]
