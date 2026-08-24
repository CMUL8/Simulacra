import { useEffect, useMemo, useState } from "react";

type Config = { title: string; subtitle: string };
type Row = Record<string, string | number>;
type Named = { id: string; name: string };
type Entity = Named & { fields?: Array<{ name: string; type: string }> };
type Workflow = Named & { states?: string[]; initial_state?: string };
type OperationGraph = {
  metadata?: { name?: string; description?: string; version?: number };
  entities?: Entity[];
  workflows?: Workflow[];
  views?: Named[];
  agents?: Named[];
  automations?: Named[];
  approval_rules?: Named[];
};

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(url);
    if (!response.ok) return fallback;
    const text = await response.text();
    if (!text.trim() || text.trim().startsWith("<")) return fallback;
    return JSON.parse(text) as T;
  } catch {
    return fallback;
  }
}

/** Neutral Operation Graph renderer. The selected builder may replace it with a bespoke app. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [graph, setGraph] = useState<OperationGraph>({});

  useEffect(() => {
    const base = import.meta.env.BASE_URL || "/";
    const asset = (name: string) => `${base}${name.replace(/^\//, "")}`;
    Promise.all([
      fetchJson<Config | null>(asset("config.json"), null),
      fetchJson<Row[]>(asset("data.json"), []),
      fetchJson<OperationGraph>(asset("operation-graph.json"), {}),
    ]).then(([nextConfig, data, nextGraph]) => {
      setConfig(nextConfig || { title: nextGraph.metadata?.name || "Operations", subtitle: "" });
      setRows(Array.isArray(data) ? data : []);
      setGraph(nextGraph);
    });
  }, []);

  const entity = graph.entities?.[0];
  const workflow = graph.workflows?.[0];
  const columns = useMemo(() => rows.length
    ? Object.keys(rows[0]).slice(0, 6)
    : (entity?.fields || []).map((field) => field.name).slice(0, 6), [entity, rows]);

  if (!config) return <div className="boot">Loading workspace…</div>;

  return <div className="app">
    <header className="topbar">
      <div className="brand-mark" aria-hidden="true">CM</div>
      <div className="brand-copy"><h1>{config.title}</h1><p>{config.subtitle || graph.metadata?.description || "Operational workspace"}</p></div>
      <span className="revision">Graph v{graph.metadata?.version ?? 0}</span>
    </header>
    <main>
      <section className="metrics" aria-label="Workspace summary">
        <article><span>Records</span><strong>{rows.length}</strong></article>
        <article><span>Workflows</span><strong>{graph.workflows?.length ?? 0}</strong></article>
        <article><span>Automations</span><strong>{graph.automations?.length ?? 0}</strong></article>
        <article><span>Approvals</span><strong>{graph.approval_rules?.length ?? 0}</strong></article>
      </section>
      <div className="workspace-grid">
        <section className="panel records-panel">
          <div className="panel-heading"><div><span>Primary queue</span><h2>{entity?.name || "Records"}</h2></div><button type="button" disabled>New record</button></div>
          {rows.length ? <div className="table-wrap"><table><thead><tr>{columns.map((column) => <th key={column}>{column}</th>)}</tr></thead>
            <tbody>{rows.slice(0, 12).map((row, index) => <tr key={index}>{columns.map((column) => <td key={column}>{String(row[column] ?? "")}</td>)}</tr>)}</tbody></table></div>
          : <div className="empty-state"><strong>No records yet</strong><p>The workspace is ready. Records will appear here when users or approved automations create them.</p></div>}
        </section>
        <aside className="panel workflow-panel"><span className="eyebrow">Workflow</span><h2>{workflow?.name || "No workflow configured"}</h2>
          <ol className="states">{(workflow?.states || []).map((state, index) => <li key={state} className={state === workflow?.initial_state ? "current" : ""}><i>{index + 1}</i><span>{state.replaceAll("_", " ")}</span></li>)}</ol>
        </aside>
        <section className="panel contract-panel">
          <div><span className="eyebrow">Agents</span><strong>{graph.agents?.map((item) => item.name).join(", ") || "None"}</strong></div>
          <div><span className="eyebrow">Automations</span><strong>{graph.automations?.map((item) => item.name).join(", ") || "None"}</strong></div>
          <div><span className="eyebrow">Views</span><strong>{graph.views?.map((item) => item.name).join(", ") || "None"}</strong></div>
        </section>
      </div>
    </main>
  </div>;
}
