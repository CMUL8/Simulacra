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
        setRows(data);
        setAnalytics(stats);
      })
      .catch((err) => setBootError(String(err?.message || err)));
  }, []);

  const k = analytics?.kpis || {};
  const topVendors = useMemo(
    () => [...(analytics?.vendor_scores || [])].sort((a, b) => b.max_score - a.max_score).slice(0, 5),
    [analytics],
  );
  const themes = (analytics?.theme_breakdown || []).slice(0, 5);
  const risks = analytics?.risk_distribution || [];

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
              <p className="note">No findings yet — re-ingest sources to populate this deck.</p>
            </>
          ),
        },
      ];
    }
    return [
      {
        id: "title",
        node: (
          <>
            <p className="eyebrow">Slide deck</p>
            <h1>{config.title}</h1>
            <p className="sub">{config.subtitle}</p>
            <p className="note">{rows.length} findings in scope</p>
          </>
        ),
      },
      {
        id: "kpis",
        node: (
          <>
            <p className="eyebrow">Situation</p>
            <h2>Risk at a glance</h2>
            <div className="kpi-grid">
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
          </>
        ),
      },
      {
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
      },
      {
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
      },
      {
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
      },
      {
        id: "close",
        node: (
          <>
            <p className="eyebrow">Ask</p>
            <h2>Next</h2>
            <p className="sub">
              Clear high-risk vendors first, then refresh this deck when new diligence lands.
            </p>
          </>
        ),
      },
    ];
  }, [config, rows, k, topVendors, themes, risks]);

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
