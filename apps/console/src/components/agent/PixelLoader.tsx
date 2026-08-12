/** Beautiful UI–inspired pixel-grid loader with elapsed time. */
import { useEffect, useState } from "react";

type Props = {
  label?: string;
  startedAt?: number | null;
  compact?: boolean;
};

function formatElapsed(sec: number): string {
  if (sec < 10) return `${sec.toFixed(1)}s`;
  if (sec < 60) return `${Math.floor(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

export function PixelLoader({ label = "Working", startedAt, compact }: Props) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(id);
  }, []);
  const sec = startedAt ? Math.max(0, (now - startedAt) / 1000) : 0;

  return (
    <div className={`bui-loader${compact ? " compact" : ""}`} aria-live="polite">
      <div className="bui-pixel-grid" aria-hidden>
        {Array.from({ length: 9 }, (_, i) => (
          <i key={i} style={{ animationDelay: `${(i % 3) * 0.12 + Math.floor(i / 3) * 0.08}s` }} />
        ))}
      </div>
      <div className="bui-loader-copy">
        <span className="bui-loader-label">{label}</span>
        {startedAt != null ? <span className="bui-loader-time">{formatElapsed(sec)}</span> : null}
      </div>
    </div>
  );
}
