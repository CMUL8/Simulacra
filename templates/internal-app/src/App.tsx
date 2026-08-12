import { useEffect, useMemo, useState } from "react";

type Config = { title: string; subtitle: string };
type Row = Record<string, string | number>;

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

/** Minimal canvas — Prime authors the real artifact on Build. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rows, setRows] = useState<Row[]>([]);

  useEffect(() => {
    const base = import.meta.env.BASE_URL || "/";
    const asset = (name: string) => `${base}${name.replace(/^\//, "")}`;
    Promise.all([
      fetchJson<Config | null>(asset("config.json"), null),
      fetchJson<Row[]>(asset("data.json"), []),
    ]).then(([cfg, data]) => {
      setConfig(cfg || { title: "App", subtitle: "" });
      setRows(Array.isArray(data) ? data : []);
    });
  }, []);

  const columns = useMemo(() => {
    const keys = new Set<string>();
    for (const row of rows.slice(0, 20)) Object.keys(row).forEach((k) => keys.add(k));
    return [...keys].slice(0, 6);
  }, [rows]);

  if (!config) return <div className="boot">Loading…</div>;

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
      </header>
      <main className="overview">
        <section className="panel">
          <h2>Canvas</h2>
          <p className="muted">
            Starter shell only. Build with the agent so layout, sections, and craft match the ask.
          </p>
          {!rows.length ? (
            <p className="muted">No rows yet — attach sources or research, then rebuild.</p>
          ) : (
            <table>
              <thead>
                <tr>
                  {columns.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, 8).map((row, i) => (
                  <tr key={i}>
                    {columns.map((c) => (
                      <td key={c}>{String(row[c] ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </main>
    </div>
  );
}
