/** Beautiful UI–inspired pixel-grid loader with elapsed time. */
import { useEffect, useState } from "react";

type Props = {
  label?: string;
  startedAt?: number | null;
  compact?: boolean;
  /** Just the 3×3 grid — use as thinking glyph. */
  iconOnly?: boolean;
};

function formatElapsed(sec: number): string {
  if (sec < 10) return `${sec.toFixed(1)}s`;
  if (sec < 60) return `${Math.floor(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}

function PixelGrid({ compact }: { compact?: boolean }) {
  return (
    <div className={`bui-pixel-grid${compact ? " compact" : ""}`} aria-hidden>
      {Array.from({ length: 9 }, (_, i) => (
        <i key={i} style={{ animationDelay: `${(i % 3) * 0.12 + Math.floor(i / 3) * 0.08}s` }} />
      ))}
    </div>
  );
}

export function PixelLoader({ label = "Working", startedAt, compact, iconOnly }: Props) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(id);
  }, []);
  const sec = startedAt ? Math.max(0, (now - startedAt) / 1000) : 0;

  if (iconOnly) {
    return <PixelGrid compact={compact ?? true} />;
  }

  return (
    <div className={`bui-loader${compact ? " compact" : ""}`} aria-live="polite">
      <PixelGrid compact={compact} />
      <div className="bui-loader-copy">
        <span className="bui-loader-label">{label}</span>
        {startedAt != null ? <span className="bui-loader-time">{formatElapsed(sec)}</span> : null}
      </div>
    </div>
  );
}

export { PixelGrid, formatElapsed };
