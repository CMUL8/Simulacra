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

	def __init__(self, ttl_seconds: int = 181):
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
		# Actor and status are server-derived; callers cannot assert an away/offline state.
		presence = Presence(tenant_id, project_id, actor_id, "online", None, instant.isoformat(),
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
				(replace_status(value, instant) for key, value in self._entries.items() if key[:2] == (tenant_id, project_id)),
				key=lambda value: value.actor_id,
			)

	def leave(self, *, tenant_id: str, project_id: str, actor_id: str) -> None:
		with self._lock:
			self._entries.pop((tenant_id, project_id, actor_id), None)


def replace_status(value: Presence, now: datetime) -> Presence:
	"""Map the server-clock heartbeat age to the only three public states."""
	age = (now - datetime.fromisoformat(value.last_seen_at).astimezone(UTC)).total_seconds()
	status = "online" if age <= 45 else "away" if age <= 180 else "offline"
	return Presence(value.tenant_id, value.project_id, value.actor_id, status, None, value.last_seen_at, value.expires_at)
