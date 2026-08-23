import { Activity, AppWindow, Bot, Check, ChevronRight, CircleAlert, GitBranch, RefreshCw, RotateCcw, Search, Siren } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { FeatureState } from "../shared";
import type { ActionItem, EntityDetail, InventoryItem, ObservabilityKind, ObservabilityProps } from "./contracts";
import "./observability.css";

type View = "overview" | "inventory" | "detail" | "actions";
const KINDS: Array<{ id: ObservabilityKind; label: string; icon: typeof AppWindow }> = [
  { id: "application", label: "Applications", icon: AppWindow }, { id: "workflow", label: "Workflows", icon: GitBranch }, { id: "agent", label: "Agents", icon: Bot },
];
const KIND_LABEL: Record<ObservabilityKind, string> = { application: "Application", workflow: "Workflow", agent: "Agent" };

function readDeepLink(value?: string): { view: View; kind?: ObservabilityKind; id?: string; trace?: string } {
  if (!value) return { view: "overview" };
  const params = new URLSearchParams(value.includes("?") ? value.split("?", 2)[1] : value);
  const rawView = params.get("obsView");
  const view: View = rawView === "inventory" || rawView === "detail" || rawView === "actions" ? rawView : "overview";
  const rawKind = params.get("obsKind");
  const kind = rawKind === "application" || rawKind === "workflow" || rawKind === "agent" ? rawKind : undefined;
  return { view: view === "detail" && (!kind || !params.get("obsId")) ? "overview" : view, kind, id: params.get("obsId") ?? undefined, trace: params.get("trace") ?? undefined };
}
function metric(value: number, suffix = "") { return `${Number.isInteger(value) ? value : value.toFixed(2)}${suffix}`; }
function ago(iso: string) { const seconds = Math.max(0, Math.floor((Date.now() - Date.parse(iso)) / 1000)); if (seconds < 60) return `${seconds}s ago`; if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`; if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`; return `${Math.floor(seconds / 86_400)}d ago`; }

export function ObservabilityConsole({ snapshot, state = "ready", initialDeepLink, canIntervene = false, adapter, errorMessage }: ObservabilityProps) {
  const initial = readDeepLink(initialDeepLink);
  const [view, setView] = useState<View>(initial.view);
  const [kind, setKind] = useState<ObservabilityKind>(initial.kind ?? "application");
  const [selected, setSelected] = useState<{ kind: ObservabilityKind; id: string; trace?: string } | null>(initial.id && initial.kind ? { kind: initial.kind, id: initial.id, trace: initial.trace } : null);
  const [detail, setDetail] = useState<EntityDetail | null>(null);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "error">("idle");
  const [search, setSearch] = useState("");
  const [health, setHealth] = useState<"all" | InventoryItem["health"]>("all");
  const [pendingAction, setPendingAction] = useState<string | null>(null);

  useEffect(() => { const next = readDeepLink(initialDeepLink); setView(next.view); if (next.kind) setKind(next.kind); setSelected(next.id && next.kind ? { kind: next.kind, id: next.id, trace: next.trace } : null); }, [initialDeepLink]);
  useEffect(() => {
    if (!selected || !snapshot) { setDetail(null); return; }
    const cached = snapshot.details?.[`${selected.kind}:${selected.id}`];
    if (cached) { setDetail(cached); setDetailState("idle"); return; }
    if (!adapter?.loadDetail) { setDetail(null); return; }
    let active = true; setDetailState("loading");
    adapter.loadDetail(selected.kind, selected.id).then((value) => { if (active) { setDetail(value); setDetailState("idle"); } }).catch(() => { if (active) setDetailState("error"); });
    return () => { active = false; };
  }, [selected, snapshot, adapter]);

  const inventory = useMemo(() => (snapshot?.inventories[kind] ?? []).filter((item) => (health === "all" || item.health === health) && `${item.name} ${item.id}`.toLowerCase().includes(search.trim().toLowerCase())), [snapshot, kind, health, search]);
  if (state !== "ready" || !snapshot) return <div className="obs-shell"><FeatureState state={state === "ready" ? "empty" : state} title={state === "error" ? "Telemetry unavailable" : undefined} detail={errorMessage} onRetry={adapter?.refresh ? () => void adapter.refresh?.() : undefined} /></div>;

  function navigate(next: View) { setView(next); adapter?.openDeepLink?.(`?obsView=${next}`); }
  function openItem(item: InventoryItem, trace?: string) { setKind(item.kind); setSelected({ kind: item.kind, id: item.id, trace }); setView("detail"); adapter?.openDeepLink?.(`${item.deep_link}${trace ? `&trace=${encodeURIComponent(trace)}` : ""}`); }
  async function action(item: ActionItem, mode: "acknowledge" | "retry") { const fn = mode === "retry" ? adapter?.retryAction : adapter?.acknowledgeAction; if (!fn) return; setPendingAction(item.id); try { await fn(item.id); } finally { setPendingAction(null); } }

  return <main className="obs-shell" aria-labelledby="obs-title">
    <header className="obs-header"><div><span>Enterprise telemetry</span><h1 id="obs-title">Observability</h1><p>Operational truth across applications, workflows, and agents.</p></div><div><time>Updated {ago(snapshot.generated_at)}</time>{adapter?.refresh ? <button type="button" onClick={() => void adapter.refresh?.()}><RefreshCw size={13} /> Refresh</button> : null}</div></header>
    <nav className="obs-nav" aria-label="Observability views"><button type="button" aria-current={view === "overview" ? "page" : undefined} onClick={() => navigate("overview")}><Activity size={14} /> Overview</button><button type="button" aria-current={view === "inventory" || view === "detail" ? "page" : undefined} onClick={() => navigate("inventory")}><AppWindow size={14} /> Inventory</button><button type="button" aria-current={view === "actions" ? "page" : undefined} onClick={() => navigate("actions")}><Siren size={14} /> Action Center <em>{snapshot.actions.length}</em></button></nav>

    {view === "overview" ? <Overview snapshot={snapshot} onInventory={(next) => { setKind(next); navigate("inventory"); }} onActions={() => navigate("actions")} /> : null}
    {view === "inventory" ? <section className="obs-inventory" aria-labelledby="obs-inventory-title"><div className="obs-section-head"><div><span>Live inventory</span><h2 id="obs-inventory-title">{KINDS.find((item) => item.id === kind)?.label}</h2></div><div className="obs-filter"><label><Search size={13} /><span className="obs-sr">Search inventory</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name or ID" /></label><label><span className="obs-sr">Health filter</span><select value={health} onChange={(event) => setHealth(event.target.value as typeof health)}><option value="all">All health</option><option value="failing">Failing</option><option value="degraded">Degraded</option><option value="healthy">Healthy</option><option value="inactive">Inactive</option></select></label></div></div><div className="obs-kind-tabs" role="tablist" aria-label="Inventory type">{KINDS.map(({ id, label, icon: Icon }) => <button type="button" role="tab" aria-selected={kind === id} key={id} onClick={() => setKind(id)}><Icon size={13} /> {label} <span>{snapshot.inventories[id].length}</span></button>)}</div><InventoryTable items={inventory} onOpen={openItem} /></section> : null}
    {view === "detail" ? <DetailView detail={detail} state={detailState} selected={selected} onBack={() => navigate("inventory")} onRetry={() => setSelected(selected ? { ...selected } : null)} /> : null}
    {view === "actions" ? <ActionCenter items={snapshot.actions} canIntervene={canIntervene} pending={pendingAction} onOpen={(item) => { const found = snapshot.inventories[item.entity_kind].find((candidate) => candidate.id === item.entity_id); if (found) openItem(found, item.trace_id); else adapter?.openDeepLink?.(item.deep_link); }} onAcknowledge={(item) => void action(item, "acknowledge")} onRetry={(item) => void action(item, "retry")} supportsAcknowledge={Boolean(adapter?.acknowledgeAction)} supportsRetry={Boolean(adapter?.retryAction)} /> : null}
  </main>;
}

function Overview({ snapshot, onInventory, onActions }: { snapshot: NonNullable<ObservabilityProps["snapshot"]>; onInventory: (kind: ObservabilityKind) => void; onActions: () => void }) {
  const peak = Math.max(1, ...snapshot.overview.trend.map((item) => item.runs));
  return <section className="obs-overview" aria-labelledby="obs-overview-title"><h2 id="obs-overview-title" className="obs-sr">Telemetry overview</h2><dl className="obs-metrics"><div><dt>Runs</dt><dd>{snapshot.overview.runs}</dd></div><div><dt>Success rate</dt><dd>{metric(snapshot.overview.success_rate, "%")}</dd></div><div><dt>Errors</dt><dd>{snapshot.overview.errors}</dd></div><div><dt>P95 latency</dt><dd>{metric(snapshot.overview.p95_ms, " ms")}</dd></div></dl>
    <div className="obs-overview__split"><section className="obs-trend"><header><div><span>Selected window</span><h3>Run volume and errors</h3></div><small>{snapshot.overview.trend.length} intervals</small></header>{snapshot.overview.trend.length ? <ol aria-label="Run volume by interval">{snapshot.overview.trend.map((point) => <li key={point.start_at}><time>{new Date(point.start_at).toLocaleString([], { month: "short", day: "numeric", hour: "numeric" })}</time><span className="obs-trend__track"><i style={{ width: `${(point.runs / peak) * 100}%` }} /><b style={{ width: `${(point.errors / peak) * 100}%` }} /></span><strong>{point.runs}</strong><em>{point.errors} err</em></li>)}</ol> : <p className="obs-empty">No telemetry in this window.</p>}</section>
      <aside className="obs-health"><header><div><span>Entity health</span><h3>Fleet state</h3></div><button type="button" onClick={onActions}>Open actions <ChevronRight size={13} /></button></header><dl>{(["failing", "degraded", "healthy", "inactive"] as const).map((item) => <div key={item}><dt><i className={`obs-dot obs-dot--${item}`} />{item}</dt><dd>{snapshot.overview.health_counts[item] ?? 0}</dd></div>)}</dl><div className="obs-fleet-links"><button type="button" onClick={() => onInventory("application")}><AppWindow size={13} /> {snapshot.overview.active_applications} apps</button><button type="button" onClick={() => onInventory("workflow")}><GitBranch size={13} /> {snapshot.overview.active_workflows} workflows</button><button type="button" onClick={() => onInventory("agent")}><Bot size={13} /> {snapshot.overview.active_agents} agents</button></div></aside></div>
  </section>;
}

function InventoryTable({ items, onOpen }: { items: InventoryItem[]; onOpen: (item: InventoryItem) => void }) {
  if (!items.length) return <p className="obs-empty">No entities match this filter.</p>;
  return <div className="obs-table-wrap"><table className="obs-table"><thead><tr><th scope="col">Name</th><th scope="col">Health</th><th scope="col">Runs</th><th scope="col">Success</th><th scope="col">P95</th><th scope="col">Last seen</th><th scope="col"><span className="obs-sr">Open</span></th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><th scope="row"><strong>{item.name}</strong><small>{item.id} · {item.environments.join(", ")}</small></th><td><span className={`obs-health-label obs-health-label--${item.health}`}><i className={`obs-dot obs-dot--${item.health}`} />{item.health}</span></td><td>{item.runs}</td><td>{metric(item.success_rate, "%")}</td><td>{metric(item.p95_ms, " ms")}</td><td>{ago(item.last_seen_at)}</td><td><button type="button" onClick={() => onOpen(item)} aria-label={`Open ${item.name}`}><ChevronRight size={14} /></button></td></tr>)}</tbody></table></div>;
}

function DetailView({ detail, state, selected, onBack, onRetry }: { detail: EntityDetail | null; state: "idle" | "loading" | "error"; selected: { kind: ObservabilityKind; id: string; trace?: string } | null; onBack: () => void; onRetry: () => void }) {
  return <section className="obs-detail" aria-labelledby="obs-detail-title"><button type="button" className="obs-back" onClick={onBack}>← Inventory</button>{state === "loading" ? <FeatureState state="loading" title="Loading telemetry detail" /> : state === "error" ? <FeatureState state="error" title="Detail unavailable" onRetry={onRetry} /> : !detail ? <FeatureState state="empty" title="No detail telemetry" detail={selected ? `No events found for ${selected.id}.` : undefined} /> : <><header><div><span>{KIND_LABEL[detail.item.kind]}</span><h2 id="obs-detail-title">{detail.item.name}</h2><p>{detail.item.id} · {detail.item.environments.join(", ")}</p></div><span className={`obs-health-label obs-health-label--${detail.item.health}`}><i className={`obs-dot obs-dot--${detail.item.health}`} />{detail.item.health}</span></header><dl className="obs-detail__metrics"><div><dt>Runs</dt><dd>{detail.item.runs}</dd></div><div><dt>Errors</dt><dd>{detail.item.errors}</dd></div><div><dt>Success</dt><dd>{metric(detail.item.success_rate, "%")}</dd></div><div><dt>P95</dt><dd>{metric(detail.item.p95_ms, " ms")}</dd></div></dl><div className="obs-detail__body"><section><h3>Recent events</h3><ol className="obs-events">{detail.recent_events.map((event) => <li key={event.id} className={selected?.trace === event.trace_id ? "is-selected" : ""}><i className={`obs-dot obs-dot--${event.status === "failed" ? "failing" : event.status === "warning" ? "degraded" : "healthy"}`} /><span><strong>{event.signal}</strong><small>{event.message || event.trace_id || "No trace ID"}</small></span><time>{ago(event.started_at)}</time><em>{metric(event.duration_ms, " ms")}</em></li>)}</ol></section><aside><h3>Relationships</h3><Relationship label="Applications" values={detail.related_applications} /><Relationship label="Workflows" values={detail.related_workflows} /><Relationship label="Agents" values={detail.related_agents} /><Relationship label="Traces" values={detail.trace_ids} /></aside></div></>}</section>;
}
function Relationship({ label, values }: { label: string; values: string[] }) { return <div className="obs-relation"><strong>{label}</strong>{values.length ? <ul>{values.map((value) => <li key={value}>{value}</li>)}</ul> : <span>None</span>}</div>; }

function ActionCenter({ items, canIntervene, pending, onOpen, onAcknowledge, onRetry, supportsAcknowledge, supportsRetry }: { items: ActionItem[]; canIntervene: boolean; pending: string | null; onOpen: (item: ActionItem) => void; onAcknowledge: (item: ActionItem) => void; onRetry: (item: ActionItem) => void; supportsAcknowledge: boolean; supportsRetry: boolean }) {
  return <section className="obs-actions" aria-labelledby="obs-actions-title"><header className="obs-section-head"><div><span>Prioritized interventions</span><h2 id="obs-actions-title">Action Center</h2></div><p>{items.length} open finding{items.length === 1 ? "" : "s"}</p></header>{!items.length ? <FeatureState state="empty" title="No intervention required" detail="No failures or latency regressions were detected in this window." /> : <ol>{items.map((item) => <li key={item.id}><div className="obs-action__severity"><CircleAlert size={15} /><span>{item.severity}</span></div><div className="obs-action__body"><h3>{item.title}</h3><p>{item.rationale}</p><small>{KIND_LABEL[item.entity_kind]} · {item.entity_id} · {ago(item.last_seen_at)}</small></div><div className="obs-action__controls"><button type="button" onClick={() => onOpen(item)}>Investigate</button><button type="button" disabled={!canIntervene || !supportsAcknowledge || pending === item.id} onClick={() => onAcknowledge(item)}><Check size={13} /> Acknowledge</button><button type="button" disabled={!canIntervene || !supportsRetry || pending === item.id} onClick={() => onRetry(item)}><RotateCcw size={13} /> Retry</button></div></li>)}</ol>}{!canIntervene ? <p className="obs-permission" role="note">You can investigate findings. Operator permission is required to acknowledge or retry.</p> : null}</section>;
}
