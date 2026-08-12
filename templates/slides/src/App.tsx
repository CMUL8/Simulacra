import { useEffect, useMemo, useState } from "react";

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

/** Minimal deck canvas — Prime authors slides on Build. */
export default function App() {
  const [config, setConfig] = useState<Config | null>(null);
  const [rowCount, setRowCount] = useState(0);
  const [idx, setIdx] = useState(0);

  useEffect(() => {
    const base = import.meta.env.BASE_URL || "/";
    const asset = (name: string) => `${base}${name.replace(/^\//, "")}`;
    Promise.all([
      fetchJson<Config | null>(asset("config.json"), null),
      fetchJson<unknown[]>(asset("data.json"), []),
    ]).then(([cfg, data]) => {
      setConfig(cfg || { title: "Deck", subtitle: "" });
      setRowCount(Array.isArray(data) ? data.length : 0);
    });
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
