import { useEffect, useMemo, useRef } from "react";
import type { AgentEvent } from "../api";

type Props = {
  events: AgentEvent[];
  /** Keep last N lines visible (Cursor-style faint trail). */
  limit?: number;
  live?: boolean;
};

const NOISE = /^(session ready|turn finished|agent started|agent|working)$/i;

/** Cursor-like faint progress + action lines from SSE tool/think/phase events. */
export function ActivityFeed({ events, limit = 8, live = true }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  const lines = useMemo(() => {
    const out: { id: string; text: string; running: boolean }[] = [];
    for (const e of events) {
      if (e.type === "done" || e.type === "error" || e.type === "message") continue;
      const label = (e.label || "").trim();
      if (!label || NOISE.test(label) || label.startsWith("Sandbox:")) continue;
      out.push({
        id: e.id || `${e.type}:${label}:${e.ts}`,
        text: label,
        running: e.status === "running",
      });
    }
    // Dedupe consecutive identical labels
    const deduped: typeof out = [];
    for (const row of out) {
      const prev = deduped[deduped.length - 1];
      if (prev && prev.text === row.text) {
        deduped[deduped.length - 1] = row;
        continue;
      }
      deduped.push(row);
    }
    return deduped.slice(-limit);
  }, [events, limit]);

  useEffect(() => {
    if (live) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [lines, live]);

  if (!lines.length) {
    return live ? (
      <div className="activity-feed" aria-live="polite">
        <div className="activity-line running">Thinking…</div>
      </div>
    ) : null;
  }

  return (
    <div className="activity-feed" aria-live="polite">
      {lines.map((row, i) => {
        const isLast = i === lines.length - 1;
        return (
          <div
            key={row.id}
            className={`activity-line${isLast && row.running ? " running" : ""}${isLast ? " latest" : ""}`}
          >
            {row.text}
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
