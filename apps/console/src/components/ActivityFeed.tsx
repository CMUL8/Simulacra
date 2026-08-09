import { useEffect, useMemo, useRef } from "react";
import type { AgentEvent } from "../api";

type Props = {
  events: AgentEvent[];
  /** Keep last N distinct lines visible (Cursor-style faint trail). */
  limit?: number;
  live?: boolean;
};

type Line = { id: string; text: string; running: boolean; kind: "think" | "action" };

const NOISE =
  /^(session ready|turn finished|agent started|agent|working|using tool|tool|using|sandbox:.*)$/i;
const THINKING = /^thinking(\.\.\.|…|\.)?$/i;
/** Successful tool-end copy — start line already covered the action. */
const END_SPAM =
  /^(read files|updated files|search done|command finished|fetch done|turn finished)$/i;

function isThinkingLabel(label: string): boolean {
  return THINKING.test(label.trim());
}

/** Cursor-like faint progress + action lines from SSE tool/think/phase events. */
export function ActivityFeed({ events, limit = 6, live = true }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  const lines = useMemo(() => {
    const out: Line[] = [];

    const pushOrReplace = (row: Line) => {
      const prev = out[out.length - 1];
      if (!prev) {
        out.push(row);
        return;
      }
      // Heartbeat / idle: keep a single live Thinking line.
      if (row.kind === "think" && prev.kind === "think") {
        out[out.length - 1] = row;
        return;
      }
      // Real work replaces trailing Thinking (don't leave stacks of Thinking).
      if (row.kind === "action" && prev.kind === "think") {
        out[out.length - 1] = row;
        return;
      }
      // Same action text — refresh running state / id.
      if (prev.text === row.text) {
        out[out.length - 1] = row;
        return;
      }
      out.push(row);
    };

    for (const e of events) {
      if (e.type === "done" || e.type === "error" || e.type === "message") continue;
      const label = (e.label || "").trim();
      if (!label || NOISE.test(label) || label.toLowerCase().startsWith("sandbox:")) continue;
      if (END_SPAM.test(label) && e.status !== "running") continue;

      const thinking = isThinkingLabel(label);
      // Skip historical Thinking once a later action exists — only keep as live placeholder.
      pushOrReplace({
        id: e.id || `${e.type}:${label}:${e.ts}`,
        text: thinking ? "Thinking" : label,
        running: e.status === "running" || thinking,
        kind: thinking ? "think" : "action",
      });
    }

    // Prefer a short trail of DISTINCT recent actions; Thinking only as the live tip.
    const capped = out.slice(-Math.max(1, limit));
    return capped;
  }, [events, limit]);

  useEffect(() => {
    if (live) bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [lines, live]);

  if (!lines.length) {
    return live ? (
      <div className="activity-feed" aria-live="polite">
        <div className="activity-line running">Thinking</div>
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
