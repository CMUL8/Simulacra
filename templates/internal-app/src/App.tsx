import { useEffect, useMemo, useState } from "react";

type Config = { title: string; subtitle: string };
type Row = Record<string, string | number>;

/** Minimal canvas — Prime authors the real artifact on Build. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
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
        setRows(Array.isArray(data) ? data : []);
      })
      .catch((err) => setBootError(String(err?.message || err)));
  }, []);

  const columns = useMemo(() => {
    const keys = new Set<string>();
    for (const row of rows.slice(0, 20)) Object.keys(row).forEach((k) => keys.add(k));
    return [...keys].slice(0, 6);
  }, [rows]);

  if (bootError) return <div className="boot">Could not load ({bootError}).</div>;
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
