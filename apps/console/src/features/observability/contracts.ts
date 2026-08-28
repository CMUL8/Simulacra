export type ObservabilityState = "loading" | "ready" | "empty" | "error" | "forbidden";
export type ObservabilityKind = "application" | "workflow" | "agent";
export type HealthState = "healthy" | "degraded" | "failing" | "inactive";
export type Severity = "critical" | "high" | "medium" | "low";

export interface TrendPoint { start_at: string; runs: number; errors: number; p95_ms: number; }
export interface OverviewSnapshot {
  start_at?: string; end_at?: string; runs: number; errors: number; warnings: number; success_rate: number; p95_ms: number;
  active_applications: number; active_workflows: number; active_agents: number; health_counts: Record<HealthState, number>; trend: TrendPoint[];
}
export interface InventoryItem {
  id: string; name: string; kind: ObservabilityKind; health: HealthState; runs: number; errors: number; success_rate: number;
  p95_ms: number; last_seen_at: string; environments: string[]; deep_link: string;
}
export interface TelemetryEvent {
  id: string; entity_kind: ObservabilityKind; entity_id: string; entity_name: string; signal: string;
  status: "succeeded" | "failed" | "warning" | "running"; started_at: string; duration_ms: number; trace_id?: string;
  environment?: string; message?: string;
}
export interface EntityDetail {
  item: InventoryItem; recent_events: TelemetryEvent[]; related_applications: string[]; related_workflows: string[];
  related_agents: string[]; trace_ids: string[];
}
export interface ActionItem {
  id: string; severity: Severity; title: string; rationale: string; entity_kind: ObservabilityKind; entity_id: string;
  action: "inspect_trace" | "open_entity" | string; trace_id?: string; first_seen_at: string; last_seen_at: string;
  occurrences: number; deep_link: string;
}
export interface ObservabilitySnapshot {
  overview: OverviewSnapshot;
  inventories: Record<ObservabilityKind, InventoryItem[]>;
  actions: ActionItem[];
  details?: Record<string, EntityDetail>;
  generated_at: string;
}
export interface ObservabilityAdapter {
  openDeepLink(link: string): void;
  loadDetail(kind: ObservabilityKind, id: string): Promise<EntityDetail>;
  acknowledgeAction(actionId: string): Promise<void>;
  retryAction(actionId: string): Promise<void>;
  refresh(): Promise<void>;
}
export interface ObservabilityProps {
  snapshot?: ObservabilitySnapshot;
  state?: ObservabilityState;
  initialDeepLink?: string;
  canIntervene?: boolean;
  adapter?: Partial<ObservabilityAdapter>;
  errorMessage?: string;
}
