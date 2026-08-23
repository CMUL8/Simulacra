from __future__ import annotations

from pathlib import Path

import pytest

from simulacra.observability import (
	EntityKind,
	EventStatus,
	HealthState,
	InMemoryTelemetryRepository,
	JsonlTelemetryRepository,
	ObservabilityQueries,
	Severity,
	TelemetryEvent,
	TelemetryQuery,
	make_deep_link,
	parse_deep_link,
	percentile,
)


def event(
	event_id: str,
	*,
	kind: EntityKind = EntityKind.AGENT,
	entity_id: str = "agent_alpha",
	name: str = "Alpha",
	status: EventStatus = EventStatus.SUCCEEDED,
	at: str = "2026-08-23T10:00:00+00:00",
	duration: float = 100,
	tenant: str = "tenant_a",
	trace: str | None = None,
	signal: str = "run",
) -> TelemetryEvent:
	return TelemetryEvent(
		id=event_id, tenant_id=tenant, entity_kind=kind, entity_id=entity_id, entity_name=name,
		signal=signal, status=status, started_at=at, duration_ms=duration, trace_id=trace,
		application_id="app_one", workflow_id="workflow_one", agent_id="agent_alpha",
	)


@pytest.fixture()
def queries() -> ObservabilityQueries:
	events = [
		event("evt_1", duration=100, trace="trace_1"),
		event("evt_2", status=EventStatus.FAILED, at="2026-08-23T10:10:00+00:00", duration=800, trace="trace_2"),
		event("evt_3", status=EventStatus.FAILED, at="2026-08-23T10:20:00+00:00", duration=900, trace="trace_3"),
		event("evt_4", status=EventStatus.FAILED, at="2026-08-23T11:20:00+00:00", duration=1_000, trace="trace_4"),
		event("evt_5", kind=EntityKind.APPLICATION, entity_id="app_one", name="Payments", at="2026-08-23T11:30:00+00:00", duration=4_000, trace="trace_5", signal="request"),
		event("evt_6", kind=EntityKind.WORKFLOW, entity_id="workflow_one", name="Settle", at="2026-08-23T11:40:00+00:00", status=EventStatus.WARNING),
		event("evt_other", tenant="tenant_b"),
	]
	return ObservabilityQueries(InMemoryTelemetryRepository(events))


def test_event_validation_and_query_bounds() -> None:
	with pytest.raises(ValueError, match="timezone"):
		event("evt_bad", at="2026-08-23T10:00:00")
	with pytest.raises(ValueError, match="negative"):
		event("evt_bad", duration=-1)
	with pytest.raises(ValueError, match="start_at"):
		TelemetryQuery(tenant_id="tenant_a", start_at="2026-08-24T00:00:00Z", end_at="2026-08-23T00:00:00Z")
	with pytest.raises(ValueError, match="limit"):
		TelemetryQuery(tenant_id="tenant_a", limit=0)


def test_repository_is_tenant_scoped_sorted_and_idempotent() -> None:
	repository = InMemoryTelemetryRepository()
	first = event("evt_1")
	repository.append(first)
	repository.append(first)
	repository.append(event("evt_2", tenant="tenant_b", at="2026-08-24T00:00:00Z"))
	assert repository.query(TelemetryQuery(tenant_id="tenant_a")) == [first]
	with pytest.raises(ValueError, match="different data"):
		repository.append(event("evt_1", duration=999))


def test_time_status_environment_and_trace_filters(queries: ObservabilityQueries) -> None:
	rows = queries.events(TelemetryQuery(
		tenant_id="tenant_a", start_at="2026-08-23T10:15:00Z", end_at="2026-08-23T11:25:00Z",
		status=EventStatus.FAILED,
	))
	assert [row.id for row in rows] == ["evt_4", "evt_3"]
	assert queries.events(TelemetryQuery(tenant_id="tenant_a", trace_id="trace_5"))[0].entity_id == "app_one"
	assert queries.events(TelemetryQuery(tenant_id="tenant_a", environment="staging")) == ()


def test_overview_is_event_derived_with_hourly_trend(queries: ObservabilityQueries) -> None:
	overview = queries.overview(TelemetryQuery(tenant_id="tenant_a"))
	assert (overview.runs, overview.errors, overview.warnings) == (6, 3, 1)
	assert overview.success_rate == 40.0
	assert overview.p95_ms == 4_000
	assert (overview.active_applications, overview.active_workflows, overview.active_agents) == (1, 1, 1)
	assert len(overview.trend) == 2
	assert sum(point.runs for point in overview.trend) == 6
	assert overview.health_counts[HealthState.FAILING.value] == 1


def test_inventory_and_entity_detail_include_relationships_and_traces(queries: ObservabilityQueries) -> None:
	items = queries.inventory(TelemetryQuery(tenant_id="tenant_a"), EntityKind.AGENT)
	assert len(items) == 1
	assert items[0].health == HealthState.FAILING
	assert items[0].errors == 3
	assert items[0].deep_link == "?obsView=detail&obsKind=agent&obsId=agent_alpha"
	detail = queries.detail(TelemetryQuery(tenant_id="tenant_a"), EntityKind.AGENT, "agent_alpha")
	assert detail is not None
	assert detail.related_applications == ("app_one",)
	assert detail.related_workflows == ("workflow_one",)
	assert detail.trace_ids[0] == "trace_4"
	assert queries.detail(TelemetryQuery(tenant_id="tenant_a"), EntityKind.AGENT, "missing") is None


def test_action_center_prioritizes_repeated_failures_and_slow_runs(queries: ObservabilityQueries) -> None:
	actions = queries.action_center(TelemetryQuery(tenant_id="tenant_a"))
	assert [item.severity for item in actions] == [Severity.CRITICAL, Severity.MEDIUM]
	assert actions[0].occurrences == 3
	assert actions[0].action == "inspect_trace"
	assert "trace=trace_4" in actions[0].deep_link
	assert actions[1].entity_id == "app_one"


def test_deep_links_round_trip_and_reject_incomplete_detail() -> None:
	link = make_deep_link(view="detail", entity_kind=EntityKind.WORKFLOW, entity_id="workflow_one", trace_id="trace_1")
	parsed = parse_deep_link(link)
	assert parsed.view == "detail"
	assert parsed.entity_kind == EntityKind.WORKFLOW
	assert parsed.entity_id == "workflow_one"
	assert parsed.trace_id == "trace_1"
	with pytest.raises(ValueError, match="require"):
		parse_deep_link("?obsView=detail&obsKind=agent")
	with pytest.raises(ValueError, match="view"):
		parse_deep_link("?obsView=unknown")


def test_jsonl_repository_persists_isolates_and_detects_corruption(tmp_path: Path) -> None:
	repository = JsonlTelemetryRepository(tmp_path / "telemetry")
	row = event("evt_1")
	repository.append(row)
	repository.append(row)
	with pytest.raises(ValueError, match="different data"):
		repository.append(event("evt_1", duration=999))
	repository.append(event("evt_2", tenant="tenant_b"))
	reopened = JsonlTelemetryRepository(tmp_path / "telemetry")
	assert reopened.query(TelemetryQuery(tenant_id="tenant_a")) == [row]
	assert reopened.query(TelemetryQuery(tenant_id="tenant_b"))[0].tenant_id == "tenant_b"
	path = reopened._path("tenant_a")
	path.write_text("not-json\n", encoding="utf-8")
	with pytest.raises(ValueError, match="invalid telemetry store"):
		reopened.query(TelemetryQuery(tenant_id="tenant_a"))
	with pytest.raises(ValueError, match="invalid tenant"):
		reopened._path("../escape")


def test_api_payload_and_percentile_empty_state(queries: ObservabilityQueries) -> None:
	payload = queries.api_payload(TelemetryQuery(tenant_id="tenant_a"))
	assert payload["overview"]["runs"] == 6
	assert set(payload["inventories"]) == {"application", "workflow", "agent"}
	assert len(payload["actions"]) == 2
	assert percentile([]) == 0
	assert percentile([4, 1, 3, 2]) == 4
