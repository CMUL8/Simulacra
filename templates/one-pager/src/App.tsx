import { useEffect, useState } from "react";

type Config = { title: string; subtitle: string };

/** Minimal one-pager canvas — Prime authors the sheet on Build. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
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
    ])
      .then(([cfg, data]) => {
        setConfig(cfg);
        setRowCount(Array.isArray(data) ? data.length : 0);
      })
      .catch((err) => setBootError(String(err?.message || err)));
  }, []);

  if (bootError) return <div className="boot">Failed to load one-pager: {bootError}</div>;
  if (!config) return <div className="boot">Loading…</div>;

  return (
    <main className="sheet">
      <header>
        <p className="eyebrow">One-pager</p>
        <h1>{config.title}</h1>
        <p className="lede">{config.subtitle}</p>
      </header>
      <p className="empty">
        {rowCount
          ? `${rowCount} rows in room — Build with the agent to author this page.`
          : "Empty room — attach sources, then Build with the agent."}
      </p>
    </main>
  );
}
