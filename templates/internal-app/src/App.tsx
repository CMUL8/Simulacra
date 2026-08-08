import { useEffect, useMemo, useState } from "react";

type Config = {
  title: string;
  subtitle: string;
  layout?: string;
  searchEnabled: boolean;
  sortColumn: string;
  sortDirection: "asc" | "desc";
  highlightColumn: string;
};

type Row = Record<string, string | number>;
type Analytics = {
  kpis: Record<string, number>;
  risk_distribution: { level: string; count: number; pct: number }[];
  vendor_scores: {
    vendor: string;
    findings: number;
    max_score: number;
    avg_score: number;
    risk_level: string;
    themes: string[];
  }[];
  theme_breakdown: { theme: string; count: number; pct: number }[];
  sources: { file: string; count: number }[];
  score_histogram: { range: string; count: number }[];
  generated_at?: string;
};

type Tab = "overview" | "findings" | "vendors";

const RISK_COLOR: Record<string, string> = {
  high: "var(--risk-high)",
  medium: "var(--risk-med)",
  low: "var(--risk-low)",
};

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [q, setQ] = useState("");
  const [riskFilter, setRiskFilter] = useState<string>("all");
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
        setRows(data);
        setAnalytics(stats);
      })
      .catch((err) => setBootError(String(err?.message || err)));
  }, []);

  const filtered = useMemo(() => {
    let out = [...rows];
    if (riskFilter !== "all") out = out.filter((r) => r.risk_level === riskFilter);
    if (q.trim()) {
      const needle = q.toLowerCase();
      out = out.filter((r) => JSON.stringify(r).toLowerCase().includes(needle));
    }
    const col = config?.sortColumn || "risk_score";
    const dir = config?.sortDirection === "asc" ? 1 : -1;
    out.sort((a, b) => {
      const av = a[col];
      const bv = b[col];
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
    return out;
  }, [rows, q, riskFilter, config]);

  if (bootError) {
    return <div className="boot">Could not load app data ({bootError}).</div>;
  }

  if (!config || !analytics) {
    return <div className="boot">Loading…</div>;
  }

  const k = analytics.kpis;
  const maxHist = Math.max(...analytics.score_histogram.map((h) => h.count), 1);

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
          {(["overview", "findings", "vendors"] as Tab[]).map((t) => (
            <button key={t} type="button" className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
              {t.charAt(0).toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>
        <div className="topbar-meta">
          <span className="live-dot" />
          Live · {k.total_findings} findings
        </div>
      </header>

      {tab === "overview" && (
        <main className="grid-overview">
          <section className="kpi-row">
            <Kpi label="Findings" value={k.total_findings} sub="across data room" />
            <Kpi label="Vendors" value={k.unique_vendors} sub={`${k.critical_vendors} critical`} accent />
            <Kpi label="High risk" value={k.high_risk} sub={`${k.medium_risk} medium · ${k.low_risk} low`} warn />
            <Kpi label="Avg score" value={k.avg_score} sub={`peak ${k.max_score}`} />
            <Kpi label="Sources" value={k.source_files} sub="files ingested" />
          </section>

          <section className="panel span-2">
            <h2>Risk distribution</h2>
            <div className="risk-bars">
              {analytics.risk_distribution.map((r) => (
                <div key={r.level} className="risk-bar-row">
                  <span className={`pill ${r.level}`}>{r.level}</span>
                  <div className="bar-track">
                    <div
                      className={`bar-fill ${r.level}`}
                      style={{ width: `${Math.max(r.pct, 4)}%` }}
                    />
                  </div>
                  <span className="bar-val">{r.count} <em>({r.pct}%)</em></span>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>Score distribution</h2>
            <div className="histogram">
              {analytics.score_histogram.map((h) => (
                <div key={h.range} className="hist-col">
                  <div
                    className="hist-bar"
                    style={{ height: `${(h.count / maxHist) * 100}%` }}
                    title={`${h.count} findings`}
                  />
                  <span>{h.range}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="panel span-2">
            <h2>Top vendors by max risk score</h2>
            <div className="vendor-leaderboard">
              {analytics.vendor_scores.slice(0, 8).map((v, i) => (
                <div key={v.vendor} className="vendor-row">
                  <span className="rank">{i + 1}</span>
                  <div className="vendor-info">
                    <strong>{v.vendor}</strong>
                    <span>{v.findings} findings · avg {v.avg_score}</span>
                  </div>
                  <div className="score-meter">
                    <div
                      className={`score-fill ${v.risk_level}`}
                      style={{ width: `${v.max_score}%` }}
                    />
                  </div>
                  <span className={`score-num ${v.risk_level}`}>{v.max_score}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="panel">
            <h2>Theme breakdown</h2>
            <ul className="theme-list">
              {analytics.theme_breakdown.slice(0, 8).map((t) => (
                <li key={t.theme}>
                  <span>{t.theme}</span>
                  <span className="theme-bar-wrap">
                    <span className="theme-bar" style={{ width: `${t.pct}%` }} />
                  </span>
                  <span className="theme-count">{t.count}</span>
                </li>
              ))}
            </ul>
          </section>

          <section className="panel span-3">
            <h2>Data sources</h2>
            <div className="source-chips">
              {analytics.sources.map((s) => (
                <div key={s.file} className="source-chip">
                  <code>{s.file}</code>
                  <span>{s.count} rows</span>
                </div>
              ))}
            </div>
          </section>
        </main>
      )}

      {tab === "findings" && (
        <main className="findings-layout">
          <div className="findings-toolbar">
            {config.searchEnabled && (
              <input
                className="search"
                placeholder="Search vendors, themes, evidence, region…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
            )}
            <div className="filters">
              {["all", "high", "medium", "low"].map((f) => (
                <button
                  key={f}
                  type="button"
                  className={`filter ${riskFilter === f ? "on" : ""} ${f !== "all" ? f : ""}`}
                  onClick={() => setRiskFilter(f)}
                >
                  {f === "all" ? "All" : f}
                </button>
              ))}
            </div>
            <span className="result-count">{filtered.length} results</span>
          </div>
          <div className="findings-split">
            <div className="table-panel">
              <table>
                <thead>
                  <tr>
                    <th>Vendor</th>
                    <th>Theme</th>
                    <th>Risk</th>
                    <th>Score</th>
                    <th>Region</th>
                    <th>Owner</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((row, i) => (
                    <tr
                      key={i}
                      className={selected === row ? "selected" : ""}
                      onClick={() => setSelected(row)}
                    >
                      <td className="vendor-cell">{String(row.vendor)}</td>
                      <td>{String(row.theme)}</td>
                      <td><span className={`badge ${row.risk_level}`}>{String(row.risk_level)}</span></td>
                      <td className="mono">{String(row.risk_score)}</td>
                      <td>{String(row.region || "—")}</td>
                      <td>{String(row.owner || "—")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {selected && (
              <aside className="detail-panel">
                <h3>{String(selected.vendor)}</h3>
                <dl>
                  <dt>Theme</dt><dd>{String(selected.theme)}</dd>
                  <dt>Risk</dt><dd><span className={`badge ${selected.risk_level}`}>{String(selected.risk_level)}</span></dd>
                  <dt>Score</dt><dd className="mono">{String(selected.risk_score)}</dd>
                  <dt>Region</dt><dd>{String(selected.region || "—")}</dd>
                  <dt>Owner</dt><dd>{String(selected.owner || "—")}</dd>
                  <dt>Source</dt><dd><code>{String(selected.source_file)}</code></dd>
                  <dt>Evidence</dt><dd className="evidence">{String(selected.evidence)}</dd>
                </dl>
                <button type="button" className="close-detail" onClick={() => setSelected(null)}>Close</button>
              </aside>
            )}
          </div>
        </main>
      )}

      {tab === "vendors" && (
        <main className="vendors-grid">
          {analytics.vendor_scores.map((v) => (
            <article key={v.vendor} className={`vendor-card ${v.risk_level}`}>
              <header>
                <h3>{v.vendor}</h3>
                <span className={`badge ${v.risk_level}`}>{v.risk_level}</span>
              </header>
              <div className="vendor-stats">
                <div><span>{v.max_score}</span>max score</div>
                <div><span>{v.avg_score}</span>avg</div>
                <div><span>{v.findings}</span>findings</div>
              </div>
              <div className="vendor-themes">
                {v.themes.map((t) => (
                  <span key={t} className="theme-tag">{t}</span>
                ))}
              </div>
            </article>
          ))}
        </main>
      )}

      <footer className="footer">
        Simulacra · integration control layer · generated {analytics.generated_at?.slice(0, 10) ?? "today"}
      </footer>
    </div>
  );
}

function Kpi({ label, value, sub, accent, warn }: { label: string; value: number; sub: string; accent?: boolean; warn?: boolean }) {
  return (
    <div className={`kpi ${accent ? "accent" : ""} ${warn ? "warn" : ""}`}>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      <span className="kpi-sub">{sub}</span>
    </div>
  );
}
