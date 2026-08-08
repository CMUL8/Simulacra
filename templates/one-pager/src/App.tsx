import { useEffect, useMemo, useState } from "react";

type Config = { title: string; subtitle: string; layout?: string };
type Row = Record<string, string | number>;
type Analytics = {
  kpis: Record<string, number>;
  vendor_scores: {
    vendor: string;
    findings: number;
    max_score: number;
    risk_level: string;
  }[];
  theme_breakdown: { theme: string; count: number }[];
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
        setRows(data);
        setAnalytics(stats);
      })
      .catch((err) => setBootError(String(err?.message || err)));
  }, []);

  const k = analytics?.kpis || {};
  const top = useMemo(
    () => [...(analytics?.vendor_scores || [])].sort((a, b) => b.max_score - a.max_score).slice(0, 5),
    [analytics],
  );
  const themes = (analytics?.theme_breakdown || []).slice(0, 4);

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
        <p className="empty">No findings in this data room yet.</p>
      ) : (
        <>
          <section className="kpis">
            <div className="kpi warn">
              <span>High risk</span>
              <strong>{k.high_risk ?? 0}</strong>
            </div>
            <div className="kpi">
              <span>Findings</span>
              <strong>{k.total_findings ?? rows.length}</strong>
            </div>
            <div className="kpi">
              <span>Vendors</span>
              <strong>{k.vendors ?? top.length}</strong>
            </div>
            <div className="kpi">
              <span>Max score</span>
              <strong>{k.max_score ?? "—"}</strong>
            </div>
          </section>

          <div className="cols">
            <section>
              <h2>Top risks</h2>
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
          </div>

          <footer>
            <strong>So what</strong>
            <p>Clear the highest-scoring vendors first; refresh this sheet when new diligence arrives.</p>
          </footer>
        </>
      )}
    </main>
  );
}
