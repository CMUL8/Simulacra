import { useEffect, useMemo, useState } from "react";

type Config = { title: string; subtitle: string; layout?: string };
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
  vendor_scores?: {
    vendor: string;
    findings: number;
    max_score: number;
    risk_level: string;
  }[];
  theme_breakdown?: { theme: string; count: number }[];
};

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
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

  const k = analytics?.kpis || {};
  const diligence = analytics?.shape === "diligence";
  const top = useMemo(
    () => [...(analytics?.vendor_scores || [])].sort((a, b) => b.max_score - a.max_score).slice(0, 5),
    [analytics],
  );
  const themes = (analytics?.theme_breakdown || []).slice(0, 4);
  const breakdown = (analytics?.field_breakdowns || [])[0];
  const columns = useMemo(() => {
    if (analytics?.columns?.length) return analytics.columns.slice(0, 4);
    const keys = new Set<string>();
    for (const row of rows.slice(0, 20)) Object.keys(row).forEach((c) => keys.add(c));
    return [...keys].slice(0, 4);
  }, [analytics, rows]);

  if (bootError) return <div className="boot">Failed to load one-pager: {bootError}</div>;
  if (!config || !analytics) return <div className="boot">Loading…</div>;

  return (
    <main className="sheet">
      <header>
        <p className="eyebrow">One-pager</p>
        <h1>{config.title}</h1>
        <p className="lede">{config.subtitle}</p>
      </header>

      {!rows.length ? (
        <p className="empty">No rows in this data room yet.</p>
      ) : (
        <>
          <section className="kpis">
            <div className="kpi">
              <span>Rows</span>
              <strong>{k.row_count ?? rows.length}</strong>
            </div>
            <div className="kpi">
              <span>Fields</span>
              <strong>{k.field_count ?? columns.length}</strong>
            </div>
            <div className="kpi">
              <span>Sources</span>
              <strong>{k.source_files ?? 0}</strong>
            </div>
            {diligence ? (
              <div className="kpi warn">
                <span>High risk</span>
                <strong>{k.high_risk ?? 0}</strong>
              </div>
            ) : (
              <div className="kpi">
                <span>Shown</span>
                <strong>{Math.min(rows.length, 5)}</strong>
              </div>
            )}
          </section>

          <div className="cols">
            {diligence ? (
              <>
                <section>
                  <h2>Priority</h2>
                  <ol>
                    {top.map((v) => (
                      <li key={v.vendor}>
                        <span>{v.vendor}</span>
                        <em className={v.risk_level}>{v.max_score}</em>
                      </li>
                    ))}
                  </ol>
                </section>
                <section>
                  <h2>Themes</h2>
                  <ul>
                    {themes.map((t) => (
                      <li key={t.theme}>
                        <span>{t.theme}</span>
                        <em>{t.count}</em>
                      </li>
                    ))}
                  </ul>
                </section>
              </>
            ) : (
              <>
                <section>
                  <h2>{breakdown?.field || "Highlights"}</h2>
                  <ol>
                    {(breakdown?.values || []).slice(0, 5).map((v) => (
                      <li key={v.label}>
                        <span>{v.label}</span>
                        <em>{v.count}</em>
                      </li>
                    ))}
                    {!breakdown ? (
                      <li>
                        <span>{columns.join(" · ") || "Inventory"}</span>
                        <em>{rows.length}</em>
                      </li>
                    ) : null}
                  </ol>
                </section>
                <section>
                  <h2>Sample</h2>
                  <ul>
                    {rows.slice(0, 4).map((row, i) => (
                      <li key={i}>
                        <span>{String(row[columns[0] || ""] ?? Object.values(row)[0] ?? "")}</span>
                        <em>{String(row[columns[1] || ""] ?? "")}</em>
                      </li>
                    ))}
                  </ul>
                </section>
              </>
            )}
          </div>

          <footer>
            <strong>So what</strong>
            <p>
              Starter canvas — rebuild with the agent so this page matches the user topic, not scaffold
              chrome.
            </p>
          </footer>
        </>
      )}
    </main>
  );
}
