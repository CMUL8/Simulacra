import { useEffect, useState } from "react";

type Config = { title: string; subtitle: string };
type ResearchSection = { heading: string; body?: string; bullets?: string[] };
type Research = {
  title?: string;
  subtitle?: string;
  source_note?: string;
  sections?: ResearchSection[];
};

/** Minimal report canvas — Prime authors the real document on Build. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [research, setResearch] = useState<Research | null>(null);
  const [rowCount, setRowCount] = useState(0);
  const [bootError, setBootError] = useState<string | null>(null);

  useEffect(() => {
    const base = import.meta.env.BASE_URL || "/";
    const asset = (name: string) => `${base}${name.replace(/^\//, "")}`;
    Promise.all([
      fetch(asset("config.json")).then((r) => {
        if (!r.ok) throw new Error(`config ${r.status}`);
        return r.json();
      }),
      fetch(asset("data.json")).then((r) => (r.ok ? r.json() : [])),
      fetch(asset("research.json")).then((r) => (r.ok ? r.json() : null)).catch(() => null),
    ])
      .then(([cfg, data, researchBundle]) => {
        setConfig(cfg);
        setRowCount(Array.isArray(data) ? data.length : 0);
        setResearch(researchBundle);
      })
      .catch((err) => setBootError(String(err?.message || err)));
  }, []);

  if (bootError) return <div className="boot">Failed to load report: {bootError}</div>;
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
        <h2>Canvas</h2>
        <p className="section-body">
          Starter shell only. Build with the agent so sections, narrative, and craft match the ask —
          Simulacra will not invent the report structure for you.
        </p>
      </section>
    </article>
  );
}
