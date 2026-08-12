import { useEffect, useState } from "react";

type Config = { title: string; subtitle: string };

async function fetchJson<T>(url: string, fallback: T): Promise<T> {
  try {
    const r = await fetch(url);
    if (!r.ok) return fallback;
    const text = await r.text();
    const trimmed = text.trim();
    if (!trimmed || trimmed.startsWith("<")) return fallback;
    return JSON.parse(trimmed) as T;
  } catch {
    return fallback;
  }
}

/** Minimal one-pager canvas — Prime authors the sheet on Build. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rowCount, setRowCount] = useState(0);

  useEffect(() => {
    const base = import.meta.env.BASE_URL || "/";
    const asset = (name: string) => `${base}${name.replace(/^\//, "")}`;
    Promise.all([
      fetchJson<Config | null>(asset("config.json"), null),
      fetchJson<unknown[]>(asset("data.json"), []),
    ]).then(([cfg, data]) => {
      setConfig(cfg || { title: "One-pager", subtitle: "" });
      setRowCount(Array.isArray(data) ? data.length : 0);
    });
  }, []);

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
