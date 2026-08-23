"""Ephemeral presence hints; never used for authorization."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import utc_now, validate_scope_id


@dataclass(frozen=True, slots=True)
class Presence:
	tenant_id: str
	project_id: str
	actor_id: str
	status: str
	location: str | None
	last_seen_at: str
	expires_at: str


class PresenceRegistry:
	"""Process-local TTL registry with no durable repository dependency."""

	def __init__(self, ttl_seconds: int = 60):
		if ttl_seconds <= 0:
			raise ValueError("presence TTL must be positive")
		self.ttl_seconds = ttl_seconds
		self._entries: dict[tuple[str, str, str], Presence] = {}
		self._lock = threading.RLock()

	def heartbeat(
		self, *, tenant_id: str, project_id: str, actor_id: str,
		status: str = "online", location: str | None = None, now: datetime | None = None,
	) -> Presence:
		for value, label in ((tenant_id, "tenant_id"), (project_id, "project_id"), (actor_id, "actor_id")):
			validate_scope_id(value, label)
		instant = now or utc_now()
		if instant.tzinfo is None:
			raise ValueError("presence timestamp must be timezone-aware")
		instant = instant.astimezone(UTC)
		presence = Presence(tenant_id, project_id, actor_id, status, location, instant.isoformat(),
			(instant + timedelta(seconds=self.ttl_seconds)).isoformat())
		with self._lock:
			self._entries[(tenant_id, project_id, actor_id)] = presence
		return presence

	def list_active(
		self, *, tenant_id: str, project_id: str, now: datetime | None = None
	) -> list[Presence]:
		validate_scope_id(tenant_id, "tenant_id")
		validate_scope_id(project_id, "project_id")
		instant = (now or utc_now()).astimezone(UTC)
		with self._lock:
			expired = [key for key, value in self._entries.items()
				if datetime.fromisoformat(value.expires_at) <= instant]
			for key in expired:
				self._entries.pop(key, None)
			return sorted(
				(value for key, value in self._entries.items() if key[:2] == (tenant_id, project_id)),
				key=lambda value: value.actor_id,
			)

	def leave(self, *, tenant_id: str, project_id: str, actor_id: str) -> None:
		with self._lock:
			self._entries.pop((tenant_id, project_id, actor_id), None)
