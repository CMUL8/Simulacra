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
  risk_distribution?: { level: string; count: number; pct: number }[];
  vendor_scores?: {
    vendor: string;
    findings: number;
    max_score: number;
    risk_level: string;
  }[];
  theme_breakdown?: { theme: string; count: number; pct: number }[];
};

export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);
  const [idx, setIdx] = useState(0);

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
  const breakdown = (analytics?.field_breakdowns || [])[0];
  const topVendors = useMemo(
    () => [...(analytics?.vendor_scores || [])].sort((a, b) => b.max_score - a.max_score).slice(0, 5),
    [analytics],
  );
  const themes = (analytics?.theme_breakdown || []).slice(0, 5);
  const risks = analytics?.risk_distribution || [];
  const sampleCols = useMemo(() => {
    if (analytics?.columns?.length) return analytics.columns.slice(0, 4);
    const keys = new Set<string>();
    for (const row of rows.slice(0, 20)) Object.keys(row).forEach((c) => keys.add(c));
    return [...keys].slice(0, 4);
  }, [analytics, rows]);

  const slides = useMemo(() => {
    if (!config) return [];
    if (!rows.length) {
      return [
        {
          id: "empty",
          node: (
            <>
              <p className="eyebrow">Deck</p>
              <h1>{config.title}</h1>
              <p className="sub">{config.subtitle}</p>
              <p className="note">No rows yet — attach sources or research, then rebuild.</p>
            </>
          ),
        },
      ];
    }

    const base = [
      {
        id: "title",
        node: (
          <>
            <p className="eyebrow">Slide deck</p>
            <h1>{config.title}</h1>
            <p className="sub">{config.subtitle}</p>
            <p className="note">
              {rows.length} rows · {k.field_count ?? sampleCols.length} fields
            </p>
          </>
        ),
      },
      {
        id: "kpis",
        node: (
          <>
            <p className="eyebrow">Situation</p>
            <h2>At a glance</h2>
            <div className="kpi-grid">
              <div className="kpi">
                <span>Rows</span>
                <strong>{k.row_count ?? rows.length}</strong>
              </div>
              <div className="kpi">
                <span>Fields</span>
                <strong>{k.field_count ?? sampleCols.length}</strong>
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
                  <span>Sample cols</span>
                  <strong>{sampleCols.length}</strong>
                </div>
              )}
            </div>
          </>
        ),
      },
    ];

    if (diligence && risks.length) {
      base.push({
        id: "mix",
        node: (
          <>
            <p className="eyebrow">Distribution</p>
            <h2>Risk mix</h2>
            <ul className="mix">
              {risks.map((r) => (
                <li key={r.level}>
                  <span className={r.level}>{r.level}</span>
                  <strong>{r.pct}%</strong>
                  <em>{r.count}</em>
                </li>
              ))}
            </ul>
          </>
        ),
      });
      base.push({
        id: "vendors",
        node: (
          <>
            <p className="eyebrow">Focus</p>
            <h2>Top vendors</h2>
            <ol className="rank">
              {topVendors.map((v) => (
                <li key={v.vendor}>
                  <span>{v.vendor}</span>
                  <strong>{v.max_score}</strong>
                </li>
              ))}
            </ol>
          </>
        ),
      });
      if (themes.length) {
        base.push({
          id: "themes",
          node: (
            <>
              <p className="eyebrow">Patterns</p>
              <h2>Themes</h2>
              <ul className="themes">
                {themes.map((t) => (
                  <li key={t.theme}>
                    <span>{t.theme}</span>
                    <strong>{t.count}</strong>
                  </li>
                ))}
              </ul>
            </>
          ),
        });
      }
    } else if (breakdown) {
      base.push({
        id: "mix",
        node: (
          <>
            <p className="eyebrow">Distribution</p>
            <h2>{breakdown.field}</h2>
            <ul className="themes">
              {breakdown.values.slice(0, 6).map((v) => (
                <li key={v.label}>
                  <span>{v.label}</span>
                  <strong>{v.count}</strong>
                </li>
              ))}
            </ul>
          </>
        ),
      });
    }

    base.push({
      id: "close",
      node: (
        <>
          <p className="eyebrow">Ask</p>
          <h2>Next</h2>
          <p className="sub">
            Rebuild with the agent so this deck matches the topic — this scaffold is only a starter.
          </p>
        </>
      ),
    });
    return base;
  }, [config, rows, k, diligence, risks, topVendors, themes, breakdown, sampleCols]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") {
        e.preventDefault();
        setIdx((i) => Math.min(i + 1, Math.max(slides.length - 1, 0)));
      }
      if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault();
        setIdx((i) => Math.max(i - 1, 0));
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [slides.length]);

  if (bootError) return <div className="boot">Failed to load deck: {bootError}</div>;
  if (!config || !analytics) return <div className="boot">Loading deck…</div>;

  const slide = slides[idx] || slides[0];
  const total = slides.length;

  return (
    <div className="deck">
      <section className="slide" onClick={() => setIdx((i) => Math.min(i + 1, total - 1))}>
        {slide?.node}
      </section>
      <footer className="deck-bar">
        <button type="button" disabled={idx === 0} onClick={() => setIdx((i) => i - 1)}>
          Prev
        </button>
        <div className="dots">
          {slides.map((s, i) => (
            <button
              key={s.id}
              type="button"
              className={i === idx ? "dot on" : "dot"}
              aria-label={`Slide ${i + 1}`}
              onClick={() => setIdx(i)}
            />
          ))}
        </div>
        <button type="button" disabled={idx >= total - 1} onClick={() => setIdx((i) => i + 1)}>
          Next
        </button>
        <span className="count">
          {idx + 1}/{total}
        </span>
      </footer>
    </div>
  );
}
