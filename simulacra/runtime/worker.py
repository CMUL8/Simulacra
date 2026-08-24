"""Durable CMUL8 runtime-job execution over an approved RuntimePlane."""

from __future__ import annotations

import copy
import time
from typing import Any, Callable, Mapping

from .errors import RuntimeAuthorizationError
from .models import ScheduledJob
from .plane import RuntimePlane
from .security import assert_opaque_credentials


SUPPORTED_JOB_KINDS = frozenset({"workflow.transition", "action.execute", "action.retry", "agent.invoke"})


def _duration_ms(started: float) -> float:
	return round((time.monotonic() - started) * 1000, 3)


class RuntimeWorker:
	"""Claims scoped durable jobs and dispatches only fixed graph-confined verbs.

	The scheduler remains the source of truth for leasing, retries, completion and
	dead letters. Redis is intentionally not required here: it may wake workers,
	but cannot be the durable source of a job's state.
	"""

	def __init__(self, plane: RuntimePlane, worker_id: str, *, queue_reachable: Callable[[], bool] | None = None):
		if not worker_id:
			raise ValueError("worker_id is required")
		self.plane = plane
		self.worker_id = worker_id
		self.queue_reachable = queue_reachable or (lambda: True)

	def readiness(self) -> dict[str, Any]:
		base = self.plane.health.readiness()
		if base["status"] != "ready":
			return {**base, "worker": self.worker_id}
		try:
			queue_ok = bool(self.queue_reachable())
		except Exception:
			queue_ok = False
		if not queue_ok:
			return {"status": "not_ready", "service": "runtime-worker", "worker": self.worker_id, "reason": "queue transport is unreachable"}
		return {"status": "ready", "service": "runtime-worker", "worker": self.worker_id, "operation_graph_revision": self.plane.policy.revision_hash}

	def _body(self, job: ScheduledJob) -> dict[str, Any]:
		assert_opaque_credentials(job.payload, context="scheduled job payload")
		envelope = job.payload.get("_cmul8")
		if not isinstance(envelope, Mapping):
			raise RuntimeAuthorizationError("scheduled job is missing its CMUL8 envelope")
		expected = {
			"job_id": job.id,
			"tenant_id": self.plane.policy.tenant_id,
			"environment_id": self.plane.environment_id,
			"project_id": self.plane.policy.project_id,
			"operation_graph_revision": self.plane.policy.revision_hash,
		}
		if dict(envelope) != expected or job.operation_graph_version != self.plane.policy.revision_hash:
			raise RuntimeAuthorizationError("scheduled job scope or approved graph revision does not match this worker")
		return {key: copy.deepcopy(value) for key, value in job.payload.items() if key != "_cmul8"}

	@staticmethod
	def _required(body: Mapping[str, Any], key: str, expected: type[Any]) -> Any:
		value = body.get(key)
		if not isinstance(value, expected) or isinstance(value, bool) and expected is int:
			raise ValueError(f"job field {key} is required and must be {expected.__name__}")
		return value

	def _dispatch(self, job: ScheduledJob) -> Any:
		if job.kind not in SUPPORTED_JOB_KINDS:
			raise RuntimeAuthorizationError(f"unsupported runtime job kind: {job.kind}")
		body = self._body(job)
		if job.kind == "workflow.transition":
			return self.plane.workflows.transition(
				self._required(body, "instance_id", str), self._required(body, "target_state", str),
				expected_state=self._required(body, "expected_state", str),
				expected_revision=self._required(body, "expected_revision", int),
				idempotency_key=body.get("idempotency_key"),
			)
		if job.kind == "action.execute":
			return self.plane.actions.execute_approved(self._required(body, "action_id", str))
		if job.kind == "action.retry":
			return self.plane.actions.retry(self._required(body, "action_id", str))
		return self.plane.agents.invoke(
			self._required(body, "agent_id", str), self._required(body, "tool", str),
			self._required(body, "input", dict), resource=body.get("resource"),
			idempotency_key=body.get("idempotency_key"),
		)

	def _emit(self, job: ScheduledJob, *, status: str, duration_ms: float, error: Exception | None = None) -> None:
		# Do not serialize arbitrary exception text: connector errors can echo a
		# credential. Type and job identity are sufficient for traceability.
		attributes: dict[str, Any] = {"job_id": job.id, "job_kind": job.kind, "attempt": job.attempts + 1}
		if error is not None:
			attributes["error_type"] = type(error).__name__
		self.plane.telemetry.emit(
			"runtime.job.execute", duration_ms,
			attributes=attributes, status=status, trace_id=f"trace_{job.id}",
			entity_kind="application", entity_id=self.plane.policy.project_id,
			entity_name=self.plane.policy.project_id,
		)

	def run_once(self) -> ScheduledJob | None:
		job = self.plane.scheduler.claim(self.worker_id)
		if job is None:
			return None
		started = time.monotonic()
		try:
			self._dispatch(job)
		except Exception as exc:
			# Scheduler error storage remains intentionally generic so it cannot
			# become an alternate raw-credential persistence path.
			failed = self.plane.scheduler.fail(job.id, worker_id=self.worker_id, error=f"{type(exc).__name__}: runtime job execution failed")
			try:
				self._emit(job, status="failed", duration_ms=_duration_ms(started), error=exc)
			except Exception:
				# Telemetry is best-effort. Its outage must not undo terminal durable
				# scheduler state or trigger a duplicate consequential dispatch.
				pass
			return failed
		completed = self.plane.scheduler.complete(job.id, worker_id=self.worker_id)
		try:
			self._emit(job, status="succeeded", duration_ms=_duration_ms(started))
		except Exception:
			# Terminal job truth is intentionally independent of observability.
			pass
		return completed

	def run_forever(self, *, poll_seconds: float = 0.5, running: Callable[[], bool] | None = None) -> None:
		if poll_seconds <= 0:
			raise ValueError("poll_seconds must be positive")
		keep_running = running or (lambda: True)
		while keep_running():
			if self.run_once() is None:
				time.sleep(poll_seconds)
