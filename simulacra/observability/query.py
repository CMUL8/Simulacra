"""Deterministic observability queries for overview, inventory, detail, and actions."""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode

from .models import (
	ActionItem,
	DeepLink,
	EntityDetail,
	EntityKind,
	EventStatus,
	HealthState,
	InventoryItem,
	OverviewSnapshot,
	Severity,
	TelemetryEvent,
	TelemetryQuery,
	TrendPoint,
	parse_timestamp,
)
from .repository import TelemetryRepository


def percentile(values: Iterable[float], quantile: float = 0.95) -> float:
	ordered = sorted(max(0.0, float(value)) for value in values)
	if not ordered:
		return 0.0
	position = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
	return round(ordered[position], 2)


def make_deep_link(*, view: str, entity_kind: EntityKind | None = None, entity_id: str | None = None, trace_id: str | None = None) -> str:
	params = {"obsView": view}
	if entity_kind:
		params["obsKind"] = entity_kind.value
	if entity_id:
		params["obsId"] = entity_id
	if trace_id:
		params["trace"] = trace_id
	return f"?{urlencode(params)}"


def parse_deep_link(value: str) -> DeepLink:
	query = value.split("?", 1)[-1]
	params = parse_qs(query, keep_blank_values=False)
	view = params.get("obsView", ["overview"])[0]
	if view not in {"overview", "inventory", "detail", "actions"}:
		raise ValueError("invalid observability view")
	kind_raw = params.get("obsKind", [None])[0]
	kind = EntityKind(kind_raw) if kind_raw else None
	entity_id = params.get("obsId", [None])[0]
	trace_id = params.get("trace", [None])[0]
	# Reuse query validation for identifiers without introducing routing dependencies.
	TelemetryQuery(tenant_id="deep_link", entity_id=entity_id, trace_id=trace_id)
	if view == "detail" and (not kind or not entity_id):
		raise ValueError("detail links require obsKind and obsId")
	return DeepLink(view=view, entity_kind=kind, entity_id=entity_id, trace_id=trace_id)


def _health(events: list[TelemetryEvent]) -> HealthState:
	if not events:
		return HealthState.INACTIVE
	errors = sum(event.status == EventStatus.FAILED for event in events)
	warnings = sum(event.status == EventStatus.WARNING for event in events)
	if errors / len(events) >= 0.2:
		return HealthState.FAILING
	if errors or warnings:
		return HealthState.DEGRADED
	return HealthState.HEALTHY


def _inventory_item(kind: EntityKind, entity_id: str, events: list[TelemetryEvent]) -> InventoryItem:
	newest = max(events, key=lambda event: parse_timestamp(event.started_at))
	errors = sum(event.status == EventStatus.FAILED for event in events)
	succeeded = sum(event.status == EventStatus.SUCCEEDED for event in events)
	completed = succeeded + errors
	return InventoryItem(
		id=entity_id,
		name=newest.entity_name,
		kind=kind,
		health=_health(events),
		runs=len(events),
		errors=errors,
		success_rate=round((succeeded / completed * 100) if completed else 0.0, 2),
		p95_ms=percentile(event.duration_ms for event in events),
		last_seen_at=newest.started_at,
		environments=tuple(sorted({event.environment for event in events})),
		deep_link=make_deep_link(view="detail", entity_kind=kind, entity_id=entity_id),
	)


class ObservabilityQueries:
	def __init__(self, repository: TelemetryRepository):
		self.repository = repository

	def events(self, query: TelemetryQuery) -> tuple[TelemetryEvent, ...]:
		return tuple(self.repository.query(query))

	def overview(self, query: TelemetryQuery) -> OverviewSnapshot:
		events = self.repository.query(TelemetryQuery(
			tenant_id=query.tenant_id, start_at=query.start_at, end_at=query.end_at,
			environment=query.environment, limit=query.limit,
		))
		errors = sum(event.status == EventStatus.FAILED for event in events)
		warnings = sum(event.status == EventStatus.WARNING for event in events)
		succeeded = sum(event.status == EventStatus.SUCCEEDED for event in events)
		completed = succeeded + errors
		groups: dict[tuple[EntityKind, str], list[TelemetryEvent]] = defaultdict(list)
		buckets: dict[str, list[TelemetryEvent]] = defaultdict(list)
		for event in events:
			groups[(event.entity_kind, event.entity_id)].append(event)
			bucket = parse_timestamp(event.started_at).replace(minute=0, second=0, microsecond=0).isoformat()
			buckets[bucket].append(event)
		health_counts = {state.value: 0 for state in HealthState}
		for grouped in groups.values():
			health_counts[_health(grouped).value] += 1
		trend = tuple(TrendPoint(
			start_at=bucket,
			runs=len(rows),
			errors=sum(event.status == EventStatus.FAILED for event in rows),
			p95_ms=percentile(event.duration_ms for event in rows),
		) for bucket, rows in sorted(buckets.items()))
		return OverviewSnapshot(
			start_at=query.start_at, end_at=query.end_at, runs=len(events), errors=errors, warnings=warnings,
			success_rate=round((succeeded / completed * 100) if completed else 0.0, 2),
			p95_ms=percentile(event.duration_ms for event in events),
			active_applications=sum(kind == EntityKind.APPLICATION for kind, _ in groups),
			active_workflows=sum(kind == EntityKind.WORKFLOW for kind, _ in groups),
			active_agents=sum(kind == EntityKind.AGENT for kind, _ in groups),
			health_counts=health_counts, trend=trend,
		)

	def inventory(self, query: TelemetryQuery, kind: EntityKind) -> tuple[InventoryItem, ...]:
		events = self.repository.query(TelemetryQuery(
			tenant_id=query.tenant_id, start_at=query.start_at, end_at=query.end_at,
			entity_kind=kind, status=query.status, environment=query.environment, limit=query.limit,
		))
		groups: dict[str, list[TelemetryEvent]] = defaultdict(list)
		for event in events:
			groups[event.entity_id].append(event)
		items = [_inventory_item(kind, entity_id, rows) for entity_id, rows in groups.items()]
		items.sort(key=lambda item: ({HealthState.FAILING: 0, HealthState.DEGRADED: 1, HealthState.HEALTHY: 2, HealthState.INACTIVE: 3}[item.health], -item.runs, item.name.lower()))
		return tuple(items)

	def detail(self, query: TelemetryQuery, kind: EntityKind, entity_id: str) -> EntityDetail | None:
		events = self.repository.query(TelemetryQuery(
			tenant_id=query.tenant_id, start_at=query.start_at, end_at=query.end_at,
			entity_kind=kind, entity_id=entity_id, environment=query.environment, limit=query.limit,
		))
		if not events:
			return None
		return EntityDetail(
			item=_inventory_item(kind, entity_id, events), recent_events=tuple(events),
			related_applications=tuple(sorted({event.application_id for event in events if event.application_id})),
			related_workflows=tuple(sorted({event.workflow_id for event in events if event.workflow_id})),
			related_agents=tuple(sorted({event.agent_id for event in events if event.agent_id})),
			trace_ids=tuple(dict.fromkeys(event.trace_id for event in events if event.trace_id)),
		)

	def action_center(self, query: TelemetryQuery, *, slow_threshold_ms: float = 3_000) -> tuple[ActionItem, ...]:
		events = self.repository.query(TelemetryQuery(
			tenant_id=query.tenant_id, start_at=query.start_at, end_at=query.end_at,
			environment=query.environment, limit=query.limit,
		))
		groups: dict[tuple[EntityKind, str, str, str], list[TelemetryEvent]] = defaultdict(list)
		for event in events:
			if event.status == EventStatus.FAILED:
				groups[(event.entity_kind, event.entity_id, event.signal, "failure")].append(event)
			elif event.duration_ms >= slow_threshold_ms:
				groups[(event.entity_kind, event.entity_id, event.signal, "latency")].append(event)
		items: list[ActionItem] = []
		for (kind, entity_id, signal, issue), rows in groups.items():
			rows.sort(key=lambda event: parse_timestamp(event.started_at))
			latest = rows[-1]
			severity = Severity.CRITICAL if issue == "failure" and len(rows) >= 3 else Severity.HIGH if issue == "failure" else Severity.MEDIUM
			digest = hashlib.sha256(f"{kind}:{entity_id}:{signal}:{issue}".encode()).hexdigest()[:12]
			items.append(ActionItem(
				id=f"action_{digest}", severity=severity,
				title=f"{latest.entity_name}: {'repeated failures' if issue == 'failure' else 'latency regression'}",
				rationale=f"{len(rows)} {signal} {'failure' if issue == 'failure' else 'run above threshold'}{'s' if len(rows) != 1 else ''} in the selected window.",
				entity_kind=kind, entity_id=entity_id,
				action="inspect_trace" if latest.trace_id else "open_entity",
				trace_id=latest.trace_id, first_seen_at=rows[0].started_at, last_seen_at=latest.started_at,
				occurrences=len(rows), deep_link=make_deep_link(view="detail", entity_kind=kind, entity_id=entity_id, trace_id=latest.trace_id),
			))
		order = {Severity.CRITICAL: 0, Severity.HIGH: 1, Severity.MEDIUM: 2, Severity.LOW: 3}
		items.sort(key=lambda item: (order[item.severity], -parse_timestamp(item.last_seen_at).timestamp(), item.id))
		return tuple(items)

	def api_payload(self, query: TelemetryQuery) -> dict[str, Any]:
		"""Single round-trip overview payload suitable for an HTTP adapter."""
		return {
			"overview": asdict(self.overview(query)),
			"inventories": {kind.value: [asdict(item) for item in self.inventory(query, kind)] for kind in EntityKind},
			"actions": [asdict(item) for item in self.action_center(query)],
		}
