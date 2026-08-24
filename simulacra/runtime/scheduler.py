"""Durable job scheduler with leases, backoff and dead letters."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Mapping

from .errors import RuntimeAuthorizationError, RuntimeConflictError
from .models import ScheduledJob, new_id, utc_now
from .policy import ApprovedGraph
from .security import assert_opaque_credentials

_GENERIC_FAILURE = "job execution failed"
_SAFE_FAILURE_TYPES = frozenset({
	"ApprovalRequiredError",
	"CredentialPolicyError",
	"InvalidTransitionError",
	"RuntimeAuthorizationError",
	"RuntimeConflictError",
	"RuntimeScopeError",
})


def _dt(value: str) -> datetime:
	return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _stamp(value: datetime) -> str:
	return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class Scheduler:
	def __init__(self, repository: Any, policy: ApprovedGraph, environment_id: str, *, clock: Callable[[], str] = utc_now, base_backoff_seconds: float = 1.0):
		self.repository, self.policy, self.environment_id, self.clock = repository, policy, environment_id, clock
		self.base_backoff_seconds = base_backoff_seconds

	def _require_policy_revision(self, job: ScheduledJob) -> ScheduledJob:
		if job.operation_graph_version != self.policy.revision_hash:
			raise RuntimeAuthorizationError("scheduled job is bound to a different Operation Graph revision")
		return job

	def get(self, job_id: str) -> ScheduledJob:
		return self._require_policy_revision(
			self.repository.get_job(self.policy.tenant_id, self.environment_id, self.policy.project_id, job_id),
		)

	def list(self) -> list[ScheduledJob]:
		return [
			job for job in self.repository.list_jobs(self.policy.tenant_id, self.environment_id, self.policy.project_id)
			if job.operation_graph_version == self.policy.revision_hash
		]

	def enqueue(self, kind: str, payload: Mapping[str, Any], *, run_at: str | None = None, max_attempts: int = 3, idempotency_key: str | None = None) -> ScheduledJob:
		if max_attempts < 1: raise ValueError("max_attempts must be positive")
		if not isinstance(kind, str) or not kind.strip(): raise ValueError("job kind is required")
		if "_cmul8" in payload: raise ValueError("_cmul8 is reserved for the runtime job envelope")
		assert_opaque_credentials(payload, context="scheduled job payload")
		now = self.clock()
		job_id = new_id("job")
		body = copy.deepcopy(dict(payload))
		body["_cmul8"] = {
			"job_id": job_id,
			"tenant_id": self.policy.tenant_id,
			"environment_id": self.environment_id,
			"project_id": self.policy.project_id,
			"operation_graph_revision": self.policy.revision_hash,
		}
		job = ScheduledJob(job_id, self.policy.tenant_id, self.environment_id, self.policy.project_id, kind, body, run_at or now, max_attempts=max_attempts, idempotency_key=idempotency_key, operation_graph_version=self.policy.revision_hash, created_at=now, updated_at=now)
		if not idempotency_key: return self.repository.create_job(job)
		def change(state: dict[str, Any]) -> ScheduledJob:
			for row in state["jobs"].values():
				existing = ScheduledJob.from_dict(row)
				if existing.idempotency_key != idempotency_key: continue
				# Each admission receives a new envelope job id, so compare only the
				# caller-controlled body plus the graph revision bound to the job.
				existing_body = copy.deepcopy(existing.payload)
				existing_body.pop("_cmul8", None)
				if existing.kind != kind or existing_body != dict(payload) or existing.operation_graph_version != self.policy.revision_hash:
					raise RuntimeConflictError("job idempotency key reused with different input")
				return existing
			state["jobs"][job.id] = job.to_dict()
			return job
		return self.repository.mutate_project(self.policy.tenant_id, self.environment_id, self.policy.project_id, change)

	def claim(self, worker_id: str, *, lease_seconds: int = 30) -> ScheduledJob | None:
		now_text = self.clock(); now = _dt(now_text)
		def change(state: dict[str, Any]) -> ScheduledJob | None:
			recovered_ids: set[str] = set()
			for job_id in sorted(state["jobs"]):
				job = ScheduledJob.from_dict(state["jobs"][job_id])
				if job.operation_graph_version != self.policy.revision_hash: continue
				if job.status != "running": continue
				if job.lease_until is not None and _dt(job.lease_until) > now: continue
				attempts = job.attempts + 1
				dead = attempts >= job.max_attempts
				next_run = job.run_at if dead else _stamp(now + timedelta(seconds=self.base_backoff_seconds * (2 ** (attempts - 1))))
				recovered = replace(
					job,
					status="dead_letter" if dead else "queued",
					attempts=attempts,
					run_at=next_run,
					lease_owner=None,
					lease_until=None,
					last_error="worker lease expired before completion",
					revision=job.revision + 1,
					updated_at=now_text,
				)
				state["jobs"][job_id] = recovered.to_dict()
				recovered_ids.add(job_id)
			candidates: list[ScheduledJob] = []
			for job_id, row in state["jobs"].items():
				if job_id in recovered_ids: continue
				job = ScheduledJob.from_dict(row)
				if job.operation_graph_version != self.policy.revision_hash: continue
				if job.status == "queued" and _dt(job.run_at) <= now: candidates.append(job)
			if not candidates: return None
			job = sorted(candidates, key=lambda item: (item.run_at, item.created_at, item.id))[0]
			claimed = replace(job, status="running", lease_owner=worker_id, lease_until=_stamp(now + timedelta(seconds=lease_seconds)), revision=job.revision + 1, updated_at=now_text)
			state["jobs"][job.id] = claimed.to_dict()
			return claimed
		return self.repository.mutate_project(self.policy.tenant_id, self.environment_id, self.policy.project_id, change)

	def complete(self, job_id: str, *, worker_id: str) -> ScheduledJob:
		job = self.get(job_id)
		if job.status != "running" or job.lease_owner != worker_id: raise RuntimeConflictError("job is not leased by worker")
		completed = replace(job, status="succeeded", lease_owner=None, lease_until=None, revision=job.revision + 1, updated_at=self.clock())
		return self.repository.save_job(completed, job.revision)

	def fail(self, job_id: str, *, worker_id: str, error: str) -> ScheduledJob:
		# Error text originates from arbitrary handlers and may contain credentials.
		# Preserve retry/dead-letter semantics, but never make scheduler state an
		# alternate persistence channel for it.
		job = self.get(job_id)
		if job.status != "running" or job.lease_owner != worker_id: raise RuntimeConflictError("job is not leased by worker")
		attempts = job.attempts + 1; now_text = self.clock(); now = _dt(now_text)
		dead = attempts >= job.max_attempts
		next_run = job.run_at if dead else _stamp(now + timedelta(seconds=self.base_backoff_seconds * (2 ** (attempts - 1))))
		# RuntimeWorker supplies this fixed text for errors it has already typed and
		# redacted. Any caller-provided text outside this exact safe contract is
		# intentionally discarded.
		safe_type = error.removesuffix(": runtime job execution failed")
		safe_error = f"{safe_type}: runtime job execution failed" if safe_type in _SAFE_FAILURE_TYPES else _GENERIC_FAILURE
		failed = replace(job, status="dead_letter" if dead else "queued", attempts=attempts, run_at=next_run, lease_owner=None, lease_until=None, last_error=safe_error, revision=job.revision + 1, updated_at=now_text)
		return self.repository.save_job(failed, job.revision)

	def cancel(self, job_id: str, *, worker_id: str | None = None) -> ScheduledJob:
		job = self.get(job_id)
		if job.status in {"succeeded", "dead_letter", "cancelled"}:
			raise RuntimeConflictError("job is already terminal")
		if job.status == "running" and worker_id is not None and job.lease_owner != worker_id:
			raise RuntimeConflictError("job is not leased by worker")
		cancelled = replace(
			job, status="cancelled", lease_owner=None, lease_until=None,
			revision=job.revision + 1, updated_at=self.clock(),
		)
		return self.repository.save_job(cancelled, job.revision)

	def run_once(self, worker_id: str, handlers: Mapping[str, Callable[[Mapping[str, Any]], Any]]) -> ScheduledJob | None:
		job = self.claim(worker_id)
		if job is None: return None
		try:
			handlers[job.kind](copy.deepcopy(job.payload))
		except Exception:
			return self.fail(job.id, worker_id=worker_id, error=_GENERIC_FAILURE)
		return self.complete(job.id, worker_id=worker_id)

	def dead_letters(self) -> list[ScheduledJob]:
		return [job for job in self.list() if job.status == "dead_letter"]
