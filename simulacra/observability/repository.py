"""Telemetry repository contracts with in-memory and durable JSONL backends."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Protocol

from .models import TelemetryEvent, TelemetryQuery, parse_timestamp


def _matches(event: TelemetryEvent, query: TelemetryQuery) -> bool:
	if event.tenant_id != query.tenant_id:
		return False
	when = parse_timestamp(event.started_at)
	if query.start_at and when < parse_timestamp(query.start_at):
		return False
	if query.end_at and when > parse_timestamp(query.end_at):
		return False
	return not (
		(query.entity_kind and event.entity_kind != query.entity_kind)
		or (query.entity_id and event.entity_id != query.entity_id)
		or (query.status and event.status != query.status)
		or (query.environment and event.environment != query.environment)
		or (query.trace_id and event.trace_id != query.trace_id)
	)


class TelemetryRepository(Protocol):
	def append(self, event: TelemetryEvent) -> TelemetryEvent: ...
	def query(self, query: TelemetryQuery) -> list[TelemetryEvent]: ...


class InMemoryTelemetryRepository:
	def __init__(self, events: list[TelemetryEvent] | None = None):
		self._events: dict[str, TelemetryEvent] = {}
		for event in events or []:
			self.append(event)

	def append(self, event: TelemetryEvent) -> TelemetryEvent:
		current = self._events.get(event.id)
		if current and current != event:
			raise ValueError("event id already exists with different data")
		self._events[event.id] = event
		return event

	def query(self, query: TelemetryQuery) -> list[TelemetryEvent]:
		rows = [event for event in self._events.values() if _matches(event, query)]
		rows.sort(key=lambda event: (parse_timestamp(event.started_at), event.id), reverse=True)
		return rows[:query.limit]


class JsonlTelemetryRepository:
	"""Append-only tenant-isolated JSONL store with idempotent event ingestion."""

	def __init__(self, root: str | Path):
		self.root = Path(root).resolve()
		self.root.mkdir(parents=True, exist_ok=True)
		self._lock = threading.RLock()

	def _path(self, tenant_id: str, *, create: bool = False) -> Path:
		# TelemetryQuery validates tenant ids before repository reads.
		query = TelemetryQuery(tenant_id=tenant_id, limit=1)
		path = (self.root / query.tenant_id / "observability" / "events.jsonl").resolve()
		try:
			path.relative_to(self.root)
		except ValueError as exc:
			raise ValueError("telemetry path escapes repository root") from exc
		if create:
			path.parent.mkdir(parents=True, exist_ok=True)
		return path

	def append(self, event: TelemetryEvent) -> TelemetryEvent:
		path = self._path(event.tenant_id, create=True)
		encoded = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":")) + "\n"
		with self._lock:
			for existing in self._read_all(path):
				if existing.id == event.id:
					if existing != event:
						raise ValueError("event id already exists with different data")
					return event
			fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
			try:
				with os.fdopen(fd, "a", encoding="utf-8") as handle:
					fd = -1
					handle.write(encoded)
					handle.flush()
					os.fsync(handle.fileno())
			finally:
				if fd >= 0:
					os.close(fd)
		return event

	@staticmethod
	def _read_all(path: Path) -> list[TelemetryEvent]:
		if not path.exists():
			return []
		try:
			lines = path.read_text(encoding="utf-8").splitlines()
		except OSError as exc:
			raise ValueError("unable to read telemetry store") from exc
		rows: list[TelemetryEvent] = []
		for line in lines:
			try:
				rows.append(TelemetryEvent.from_dict(json.loads(line)))
			except (json.JSONDecodeError, TypeError, ValueError) as exc:
				raise ValueError("invalid telemetry store") from exc
		return rows

	def query(self, query: TelemetryQuery) -> list[TelemetryEvent]:
		path = self._path(query.tenant_id)
		with self._lock:
			rows = [event for event in self._read_all(path) if _matches(event, query)]
		rows.sort(key=lambda event: (parse_timestamp(event.started_at), event.id), reverse=True)
		return rows[:query.limit]
