import { useEffect, useMemo, useState } from "react";

type Config = { title: string; subtitle: string };

/** Minimal deck canvas — Prime authors slides on Build. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rowCount, setRowCount] = useState(0);
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
      fetch(asset("data.json")).then((r) => (r.ok ? r.json() : [])),
    ])
      .then(([cfg, data]) => {
        setConfig(cfg);
        setRowCount(Array.isArray(data) ? data.length : 0);
      })
      .catch((err) => setBootError(String(err?.message || err)));
  }, []);

  const slides = useMemo(() => {
    if (!config) return [];
    return [
      {
        id: "title",
        node: (
          <>
            <p className="eyebrow">Slide deck</p>
            <h1>{config.title}</h1>
            <p className="sub">{config.subtitle}</p>
            <p className="note">
              {rowCount ? `${rowCount} rows in room` : "Empty room"} · Build with the agent to author
              this deck
            </p>
          </>
        ),
      },
    ];
  }, [config, rowCount]);

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
  if (!config) return <div className="boot">Loading deck…</div>;

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
