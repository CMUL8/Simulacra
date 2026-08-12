import { useEffect, useState } from "react";

type Config = { title: string; subtitle: string };
type ResearchSection = { heading: string; body?: string; bullets?: string[] };
type Research = {
  title?: string;
  subtitle?: string;
  source_note?: string;
  sections?: ResearchSection[];
};

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const r = await fetch(url);
    if (!r.ok) return fallback;
    const ct = (r.headers.get("content-type") || "").toLowerCase();
    const text = await r.text();
    const trimmed = text.trim();
    if (!trimmed || trimmed.startsWith("<")) return fallback;
    if (ct && !ct.includes("json") && !trimmed.startsWith("{") && !trimmed.startsWith("[")) {
      return fallback;
    }
    return JSON.parse(trimmed) as T;
  } catch {
    return fallback;
  }
}

/** Minimal report canvas — Prime authors the real document on Build. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [research, setResearch] = useState<Research | null>(null);
  const [rowCount, setRowCount] = useState(0);

  useEffect(() => {
    const base = import.meta.env.BASE_URL || "/";
    const asset = (name: string) => `${base}${name.replace(/^\//, "")}`;
    Promise.all([
      fetchJson<Config | null>(asset("config.json"), null),
      fetchJson<unknown[]>(asset("data.json"), []),
      fetchJson<Research | null>(asset("research.json"), null),
    ]).then(([cfg, data, researchBundle]) => {
      setConfig(cfg || { title: "Report", subtitle: "" });
      setRowCount(Array.isArray(data) ? data.length : 0);
      setResearch(researchBundle);
    });
  }, []);

  if (!config) return <div className="boot">Loading report…</div>;

  const sections = research?.sections || [];
  if (sections.length > 0) {
    return (
      <article className="report narrative">
        <header className="cover">
          <p className="eyebrow">Research report</p>
          <h1>{research?.title || config.title}</h1>
          {(research?.subtitle || config.subtitle) && (
            <p className="lede">{research?.subtitle || config.subtitle}</p>
          )}
          {research?.source_note ? <p className="meta">{research.source_note}</p> : null}
        </header>
        {sections.map((section, idx) => (
          <section key={`${section.heading}-${idx}`}>
            <h2>
              {idx + 1}. {section.heading}
            </h2>
            {section.body ? <p className="section-body">{section.body}</p> : null}
            {section.bullets?.length ? (
              <ul className="narrative-bullets">
                {section.bullets.map((b, i) => (
                  <li key={i}>{b}</li>
                ))}
              </ul>
            ) : null}
          </section>
        ))}
      </article>
    );
  }

  return (
    <article className="report">
      <header className="cover">
        <p className="eyebrow">Report</p>
        <h1>{config.title}</h1>
        <p className="lede">{config.subtitle}</p>
        <p className="meta">{rowCount ? `${rowCount} rows in room` : "Empty room"}</p>
      </header>
      <section>
        <p className="muted">Build with the agent to author the full report from your sources.</p>
      </section>
    </article>
  );
}
