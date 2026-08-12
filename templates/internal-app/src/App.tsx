import { useEffect, useMemo, useState } from "react";

type Config = {
  title: string;
  subtitle: string;
  layout?: string;
  searchEnabled?: boolean;
  sortColumn?: string;
  sortDirection?: "asc" | "desc";
  columns?: string[];
};

type Row = Record<string, string | number>;
type FieldBreakdown = {
  field: string;
  values: { label: string; count: number; pct: number }[];
};
type Analytics = {
  shape?: string;
  kpis: Record<string, number>;
  columns?: string[];
  field_breakdowns?: FieldBreakdown[];
  risk_distribution?: { level: string; count: number; pct: number }[];
  vendor_scores?: {
    vendor: string;
    findings: number;
    max_score: number;
    avg_score: number;
    risk_level: string;
    themes: string[];
  }[];
  theme_breakdown?: { theme: string; count: number; pct: number }[];
  sources?: { file: string; count: number }[];
  score_histogram?: { range: string; count: number }[];
};

type Tab = "overview" | "data";

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  useEffect(() => {
    const base = import.meta.env.BASE_URL || "/";
    const asset = (name: string) => `${base}${name.replace(/^\//, "")}`;
    Promise.all([
      fetch(asset("config.json")).then((r) => {
        if (!r.ok) throw new Error(`config ${r.status}`);
        return r.json();
      }),
      fetch(asset("data.json")).then((r) => {
        if (!r.ok) throw new Error(`data ${r.status}`);
        return r.json();
      }),
      fetch(asset("analytics.json")).then((r) => {
        if (!r.ok) throw new Error(`analytics ${r.status}`);
        return r.json();
      }),
    ])
      .then(([cfg, data, stats]) => {
        setConfig(cfg);
        setRows(Array.isArray(data) ? data : []);
        setAnalytics(stats);
      })
      .catch((err) => setBootError(String(err?.message || err)));
  }, []);

  const columns = useMemo(() => {
    if (config?.columns?.length) return config.columns;
    if (analytics?.columns?.length) return analytics.columns;
    const keys = new Set<string>();
    for (const row of rows.slice(0, 40)) Object.keys(row).forEach((k) => keys.add(k));
    return [...keys].slice(0, 8);
  }, [config, analytics, rows]);

  const filtered = useMemo(() => {
    let out = [...rows];
    if (q.trim()) {
      const needle = q.toLowerCase();
      out = out.filter((r) => JSON.stringify(r).toLowerCase().includes(needle));
    }
    const col = config?.sortColumn || columns[0] || "";
    if (!col) return out;
    const dir = config?.sortDirection === "asc" ? 1 : -1;
    out.sort((a, b) => {
      const av = a[col];
      const bv = b[col];
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av ?? "").localeCompare(String(bv ?? "")) * dir;
    });
    return out;
  }, [rows, q, config, columns]);

  if (bootError) {
    return <div className="boot">Could not load app data ({bootError}).</div>;
  }
  if (!config || !analytics) {
    return <div className="boot">Loading…</div>;
  }

  const k = analytics.kpis || {};
  const diligence = analytics.shape === "diligence";
  const breakdowns = analytics.field_breakdowns || [];

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◆</span>
          <div>
            <h1>{config.title}</h1>
            <p>{config.subtitle}</p>
          </div>
        </div>
        <nav className="tabs">
          {(["overview", "data"] as Tab[]).map((t) => (
            <button key={t} type="button" className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>
        <div className="topbar-meta">
          Live · {k.row_count ?? rows.length} rows
        </div>
      </header>

      {tab === "overview" && (
        <main className="overview">
          <section className="kpi-row">
            <Kpi label="Rows" value={k.row_count ?? rows.length} sub="in data room" />
            <Kpi label="Fields" value={k.field_count ?? columns.length} sub="columns" />
            <Kpi label="Sources" value={k.source_files ?? 0} sub="files ingested" />
            {diligence ? (
              <Kpi
                label="High risk"
                value={k.high_risk ?? 0}
                sub={`${k.medium_risk ?? 0} medium · ${k.low_risk ?? 0} low`}
                warn
              />
            ) : null}
          </section>

          {!rows.length ? (
            <section className="panel">
              <h2>Empty room</h2>
              <p>Attach sources or research, then rebuild. This scaffold stays topic-neutral until you do.</p>
            </section>
          ) : (
            <div className="grid-2">
              <section className="panel">
                <h2>Field mix</h2>
                {breakdowns.length === 0 ? (
                  <p className="muted">No categorical breakdowns yet — open Data for the inventory table.</p>
                ) : (
                  breakdowns.slice(0, 2).map((fb) => (
                    <div key={fb.field} className="breakdown">
                      <h3>{fb.field}</h3>
                      <ul className="bars">
                        {fb.values.slice(0, 6).map((v) => (
                          <li key={v.label}>
                            <span>{v.label}</span>
                            <div className="bar-track">
                              <div className="bar-fill" style={{ width: `${Math.max(v.pct, 2)}%` }} />
                            </div>
                            <em>
                              {v.count} · {v.pct}%
                            </em>
                          </li>
                        ))}
                      </ul>
                    </div>
                  ))
                )}
              </section>
              <section className="panel">
                <h2>Sources</h2>
                <ul className="theme-list">
                  {(analytics.sources || []).slice(0, 8).map((s) => (
                    <li key={s.file}>
                      <strong>{s.file}</strong>
                      <span>{s.count}</span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          )}
        </main>
      )}

      {tab === "data" && (
        <main className="findings-layout">
          <div className="findings-toolbar">
            {config.searchEnabled !== false ? (
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search rows…"
              />
            ) : null}
            <span className="muted">{filtered.length} shown</span>
          </div>
          <div className="findings-split">
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    {columns.map((col) => (
                      <th key={col}>{col}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.slice(0, 200).map((row, idx) => (
                    <tr
                      key={idx}
                      className={selected === row ? "on" : ""}
                      onClick={() => setSelected(row)}
                    >
                      {columns.map((col) => (
                        <td key={col}>{String(row[col] ?? "")}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <aside className="detail">
              {selected ? (
                <>
                  <h3>Row detail</h3>
                  <dl>
                    {Object.entries(selected).map(([key, val]) => (
                      <div key={key}>
                        <dt>{key}</dt>
                        <dd>{String(val)}</dd>
                      </div>
                    ))}
                  </dl>
                </>
              ) : (
                <p className="muted">Select a row</p>
              )}
            </aside>
          </div>
        </main>
      )}
    </div>
  );
}

function Kpi({
  label,
  value,
  sub,
  warn,
}: {
  label: string;
  value: number | string;
  sub?: string;
  warn?: boolean;
}) {
  return (
    <div className={`kpi${warn ? " warn" : ""}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      {sub ? <em>{sub}</em> : null}
    </div>
  );
}
