import { useEffect, useMemo, useState } from "react";

type Config = { title: string; subtitle: string; layout?: string };
type Row = Record<string, string | number>;
type Analytics = {
  kpis: Record<string, number>;
  risk_distribution: { level: string; count: number; pct: number }[];
  vendor_scores: {
    vendor: string;
    findings: number;
    max_score: number;
    risk_level: string;
  }[];
  theme_breakdown: { theme: string; count: number; pct: number }[];
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
  const topVendors = useMemo(
    () => [...(analytics?.vendor_scores || [])].sort((a, b) => b.max_score - a.max_score).slice(0, 8),
    [analytics],
  );
  const themes = analytics?.theme_breakdown || [];
  const risks = analytics?.risk_distribution || [];

  if (bootError) return <div className="boot">Failed to load report: {bootError}</div>;
  if (!config || !analytics) return <div className="boot">Loading report…</div>;

  if (!rows.length) {
    return (
      <article className="report empty">
        <header className="cover">
          <p className="eyebrow">Report</p>
          <h1>{config.title}</h1>
          <p className="lede">{config.subtitle}</p>
          <p className="empty-note">No findings in this data room yet.</p>
        </header>
      </article>
    );
  }

  return (
    <article className="report">
      <header className="cover">
        <p className="eyebrow">Internal report</p>
        <h1>{config.title}</h1>
        <p className="lede">{config.subtitle}</p>
        <p className="meta">{rows.length} findings · {topVendors.length} vendors surfaced</p>
      </header>

      <section className="exec">
        <h2>1. Executive summary</h2>
        <p>
          This room surfaces <strong>{k.high_risk ?? 0}</strong> high-risk findings across{" "}
          <strong>{k.vendors ?? topVendors.length}</strong> vendors (max score{" "}
          <strong>{k.max_score ?? "—"}</strong>). Priority should land on the highest-scoring vendors
          below before expanding scope.
        </p>
        <div className="kpi-row">
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
            <strong>{k.vendors ?? topVendors.length}</strong>
          </div>
          <div className="kpi">
            <span>Max score</span>
            <strong>{k.max_score ?? "—"}</strong>
          </div>
        </div>
      </section>

      <section>
        <h2>2. Risk mix</h2>
        <ul className="bars">
          {risks.map((r) => (
            <li key={r.level}>
              <span className={`lvl ${r.level}`}>{r.level}</span>
              <div className="bar-track">
                <div className={`bar-fill ${r.level}`} style={{ width: `${Math.max(r.pct, 2)}%` }} />
              </div>
              <em>
                {r.count} · {r.pct}%
              </em>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2>3. Priority vendors</h2>
        <table>
          <thead>
            <tr>
              <th>Vendor</th>
              <th>Max</th>
              <th>Findings</th>
              <th>Level</th>
            </tr>
          </thead>
          <tbody>
            {topVendors.map((v) => (
              <tr key={v.vendor}>
                <td>{v.vendor}</td>
                <td>{v.max_score}</td>
                <td>{v.findings}</td>
                <td className={v.risk_level}>{v.risk_level}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section>
        <h2>4. Themes</h2>
        <ul className="theme-list">
          {themes.slice(0, 8).map((t) => (
            <li key={t.theme}>
              <strong>{t.theme}</strong>
              <span>
                {t.count} · {t.pct}%
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="close">
        <h2>5. Next steps</h2>
        <p>
          Triage high-risk vendors first, confirm evidence in the source pack, and re-ingest when new
          diligence lands.
        </p>
      </section>
    </article>
  );
}
