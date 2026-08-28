import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchCmul8Observability, fetchCmul8ObservabilityDetail } from "../../api";
import { ObservabilityConsole } from "./ObservabilityConsole";
import type { EntityDetail, ObservabilityKind, ObservabilitySnapshot, ObservabilityState } from "./contracts";

type SafeObservabilityResponse = {
  overview?: Partial<ObservabilitySnapshot["overview"]>;
  inventories?: Partial<Record<ObservabilityKind, Array<Partial<ObservabilitySnapshot["inventories"][ObservabilityKind][number]>>>>;
  generated_at?: string;
};

function safeSnapshot(value: SafeObservabilityResponse): ObservabilitySnapshot {
  const overview = value.overview ?? {};
  const inventories = value.inventories ?? {};
  const asInventory = (kind: ObservabilityKind) => (inventories[kind] ?? []).map((item) => ({
    id: item.id ?? "", name: item.name ?? "Untitled", kind,
    health: item.health ?? "inactive", runs: item.runs ?? 0, errors: item.errors ?? 0,
    success_rate: item.success_rate ?? 0, p95_ms: item.p95_ms ?? 0,
    last_seen_at: item.last_seen_at ?? new Date(0).toISOString(), environments: [], deep_link: "",
  }));
  return {
    overview: {
      runs: overview.runs ?? 0, errors: overview.errors ?? 0, warnings: overview.warnings ?? 0,
      success_rate: overview.success_rate ?? 0, p95_ms: overview.p95_ms ?? 0,
      active_applications: 0, active_workflows: 0, active_agents: 0,
      health_counts: { healthy: 0, degraded: 0, failing: 0, inactive: 0 }, trend: [],
    },
    inventories: { application: asInventory("application"), workflow: asInventory("workflow"), agent: asInventory("agent") },
    actions: [], generated_at: value.generated_at ?? new Date().toISOString(),
  };
}

function safeDetail(value: { item?: Record<string, unknown>; recent_events?: Array<Record<string, unknown>> }): EntityDetail {
  const item = value.item ?? {};
  const kind = item.kind === "workflow" || item.kind === "agent" ? item.kind : "application";
  return {
    item: {
      id: typeof item.id === "string" ? item.id : "", name: typeof item.name === "string" ? item.name : "Untitled", kind,
      health: item.health === "healthy" || item.health === "degraded" || item.health === "failing" ? item.health : "inactive",
      runs: typeof item.runs === "number" ? item.runs : 0, errors: typeof item.errors === "number" ? item.errors : 0,
      success_rate: typeof item.success_rate === "number" ? item.success_rate : 0, p95_ms: typeof item.p95_ms === "number" ? item.p95_ms : 0,
      last_seen_at: typeof item.last_seen_at === "string" ? item.last_seen_at : new Date(0).toISOString(), environments: [], deep_link: "",
    },
    recent_events: (value.recent_events ?? []).map((event) => ({
      id: typeof event.id === "string" ? event.id : "", entity_kind: kind,
      entity_id: typeof event.entity_id === "string" ? event.entity_id : "", entity_name: typeof event.entity_name === "string" ? event.entity_name : "",
      signal: typeof event.signal === "string" ? event.signal : "Progress update",
      status: event.status === "failed" || event.status === "warning" || event.status === "running" ? event.status : "succeeded",
      started_at: typeof event.started_at === "string" ? event.started_at : new Date(0).toISOString(),
      duration_ms: typeof event.duration_ms === "number" ? event.duration_ms : 0,
    })),
    related_applications: [], related_workflows: [], related_agents: [], trace_ids: [],
  };
}

export function ObservabilityContainer({ projectId }: { projectId: string }) {
  const [snapshot, setSnapshot] = useState<ObservabilitySnapshot>();
  const [state, setState] = useState<ObservabilityState>("loading");
  const [error, setError] = useState<string>();

  const refresh = useCallback(async () => {
    setState("loading");
    setError(undefined);
    try {
      const value = safeSnapshot(await fetchCmul8Observability<SafeObservabilityResponse>(projectId));
      setSnapshot(value);
      setState(value.overview.runs ? "ready" : "empty");
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Telemetry unavailable";
      setError(message);
      setState(message.includes("403") ? "forbidden" : "error");
    }
  }, [projectId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const adapter = useMemo(() => ({
    refresh,
    loadDetail: async (kind: ObservabilityKind, id: string) =>
      safeDetail(await fetchCmul8ObservabilityDetail(projectId, kind, id)),
    openDeepLink: (link: string) => {
      const url = new URL(window.location.href);
      const params = new URLSearchParams(link.startsWith("?") ? link.slice(1) : link);
      for (const key of ["obsView", "obsKind", "obsId", "trace"]) url.searchParams.delete(key);
      params.forEach((value, key) => url.searchParams.set(key, value));
      window.history.replaceState(null, "", url);
    },
  }), [projectId, refresh]);

  return <ObservabilityConsole snapshot={snapshot} state={state} errorMessage={error} initialDeepLink={window.location.search} adapter={adapter} />;
}
