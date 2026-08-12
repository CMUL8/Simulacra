/**
 * Thinking — expandable traces (Beautiful UI 02).
 * Live glyph = pixel grid (same as loader). Clean unique states only.
 */
import { ChevronDown } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { AgentEvent } from "../../api";
import { PixelLoader } from "./PixelLoader";

export type ThoughtSnapshot = {
  events: AgentEvent[];
  startedAt: number;
  endedAt: number;
};

type Tab = "steps" | "reasoning" | "search" | "coding";

type Props = {
  events: AgentEvent[];
  live?: boolean;
  startedAt?: number | null;
  endedAt?: number | null;
  onStop?: () => void;
  defaultOpen?: boolean;
};

const NOISE =
  /^(session ready|turn finished|agent started|agent|agent opening|agent replied|agent ready|research noted|quarantined|working|using tool|tool|using|sandbox:.*|ipython|notebook|python|repl|rlm|ready|building app|reading sources)$/i;

export function tracesForWait(traces: AgentEvent[], startedAt?: number | null): AgentEvent[] {
  if (!startedAt) return traces.slice(-50);
  const since = new Date(startedAt - 1500).toISOString();
  const filtered = traces.filter((e) => !e.ts || e.ts >= since);
  return filtered.length ? filtered : traces.slice(-50);
}

export function formatThoughtElapsed(sec: number): string {
  if (sec < 1) return "a moment";
  if (sec < 60) return `${Math.floor(sec)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return s ? `${m}m ${s}s` : `${m}m`;
}

function polish(label: string): string {
  const t = label.trim();
  if (/^added to sources$/i.test(t)) return "Adding to data room";
  if (/^adding to data room$/i.test(t)) return t;
  if (/^ipython$/i.test(t)) return "Working through the data";
  return t;
}

function categorize(e: AgentEvent): Tab {
  const label = (e.label || "").toLowerCase();
  const tool = String(e.meta?.tool || "").toLowerCase();
  if (e.type === "phase" || e.type === "gate") return "steps";
  if (
    label.includes("search") ||
    label.includes("web") ||
    label.includes("fetch") ||
    label.includes("gather") ||
    tool.includes("web") ||
    tool.includes("search")
  )
    return "search";
  if (
    label.includes("edit") ||
    label.includes("writ") ||
    label.includes("cod") ||
    label.includes("saving") ||
    label.includes("reading") ||
    tool.includes("edit") ||
    tool.includes("write") ||
    tool.includes("ipython") ||
    tool.includes("bash")
  )
    return "coding";
  if (e.type === "think" || label.includes("think") || label.includes("review")) return "reasoning";
  if (e.type === "tool") return "coding";
  return "steps";
}

function linesFor(events: AgentEvent[], tab: Tab): { id: string; text: string; running: boolean }[] {
  const out: { id: string; text: string; running: boolean }[] = [];
  const seen = new Set<string>();
  for (const e of events) {
    if (e.type === "done" || e.type === "error" || e.type === "message") continue;
    const raw = polish((e.label || "").trim());
    if (!raw || NOISE.test(raw)) continue;
    if (categorize(e) !== tab) continue;
    const key = raw.toLowerCase();
    if (seen.has(key)) {
      const prev = out.find((r) => r.text.toLowerCase() === key);
      if (prev) prev.running = e.status === "running";
      continue;
    }
    seen.add(key);
    out.push({
      id: e.id || `${e.type}:${raw}:${e.ts}`,
      text: raw,
      running: e.status === "running",
    });
  }
  return out.slice(-8);
}

function latestState(events: AgentEvent[]): string | null {
  for (let i = events.length - 1; i >= 0; i--) {
    const e = events[i]!;
    if (e.type === "done" || e.type === "error" || e.type === "message") continue;
    const raw = polish((e.label || "").trim());
    if (!raw || NOISE.test(raw)) continue;
    return raw;
  }
  return null;
}

const TABS: { id: Tab; label: string }[] = [
  { id: "steps", label: "Steps" },
  { id: "reasoning", label: "Reasoning" },
  { id: "search", label: "Search" },
  { id: "coding", label: "Coding" },
];

export function ThinkingTrail({
  events,
  live = false,
  startedAt,
  endedAt,
  onStop,
  defaultOpen,
}: Props) {
  const [now, setNow] = useState(() => Date.now());
  const [open, setOpen] = useState(defaultOpen ?? false);
  const [tab, setTab] = useState<Tab>("steps");

  useEffect(() => {
    if (!live) return;
    const id = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(id);
  }, [live]);

  useEffect(() => {
    if (defaultOpen != null) {
      setOpen(defaultOpen);
      return;
    }
    // Stay collapsed by default — pixel + current state is enough
    if (!live) setOpen(false);
  }, [live, defaultOpen]);

  const feedEvents = useMemo(() => tracesForWait(events, startedAt), [events, startedAt]);
  const counts = useMemo(() => {
    const c: Record<Tab, number> = { steps: 0, reasoning: 0, search: 0, coding: 0 };
    const seen: Record<Tab, Set<string>> = {
      steps: new Set(),
      reasoning: new Set(),
      search: new Set(),
      coding: new Set(),
    };
    for (const e of feedEvents) {
      if (e.type === "done" || e.type === "error" || e.type === "message") continue;
      const raw = polish((e.label || "").trim());
      if (!raw || NOISE.test(raw)) continue;
      const t = categorize(e);
      const key = raw.toLowerCase();
      if (seen[t].has(key)) continue;
      seen[t].add(key);
      c[t] += 1;
    }
    return c;
  }, [feedEvents]);

  useEffect(() => {
    if (!live || !open) return;
    const order: Tab[] = ["steps", "coding", "search", "reasoning"];
    const hit = order.find((t) => counts[t] > 0);
    if (hit && counts[tab] === 0) setTab(hit);
  }, [counts, live, open, tab]);

  const lines = useMemo(() => linesFor(feedEvents, tab), [feedEvents, tab]);
  const current = useMemo(() => latestState(feedEvents), [feedEvents]);
  const elapsedSec = useMemo(() => {
    if (!startedAt) return 0;
    const end = live ? now : endedAt || now;
    return Math.max(0, (end - startedAt) / 1000);
  }, [startedAt, endedAt, live, now]);

  const summary = live
    ? formatThoughtElapsed(elapsedSec)
    : `Thought for ${formatThoughtElapsed(elapsedSec)}`;

  return (
    <div className="bui-thinking" data-live={live ? "true" : "false"} data-open={open ? "true" : "false"}>
      <div className="bui-thinking-bar">
        <button
          type="button"
          className="bui-thinking-summary"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          {live ? (
            <PixelLoader iconOnly compact />
          ) : (
            <span className="bui-thinking-dot" aria-hidden />
          )}
          <span className="bui-thinking-label">{summary}</span>
          {live && current ? <span className="bui-thinking-now">{current}</span> : null}
          <ChevronDown size={14} className="bui-thinking-chevron" aria-hidden />
        </button>
        {live && onStop ? (
          <button type="button" className="bui-thinking-stop" onClick={onStop}>
            Stop
          </button>
        ) : null}
      </div>

      {open ? (
        <div className="bui-thinking-panel">
          <div className="bui-seg" role="tablist" aria-label="Trace kind">
            {TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                role="tab"
                aria-selected={tab === t.id}
                className={tab === t.id ? "on" : ""}
                onClick={() => setTab(t.id)}
              >
                {t.label}
                {counts[t.id] > 0 ? <em>{counts[t.id]}</em> : null}
              </button>
            ))}
          </div>
          <ul className="bui-trace-list">
            {lines.length === 0 ? (
              <li className="bui-trace-empty">{live ? "Working…" : "Nothing in this tab"}</li>
            ) : (
              lines.map((row, i) => (
                <li
                  key={row.id}
                  className={`bui-trace-line${i === lines.length - 1 && row.running ? " running" : ""}`}
                >
                  {row.text}
                </li>
              ))
            )}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
