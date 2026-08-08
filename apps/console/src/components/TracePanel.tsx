import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Loader2,
  XCircle,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { AgentEvent } from "../api";

type Props = {
  events: AgentEvent[];
  compact?: boolean;
  onCancel?: () => void;
};

/** Collapse running→done duplicates: one row per label (latest wins). */
function collapseEvents(events: AgentEvent[]): AgentEvent[] {
  const order: string[] = [];
  const byKey = new Map<string, AgentEvent>();
  for (const e of events) {
    if (e.type === "done" || e.type === "error" || e.type === "message") continue;
    // Drop noisy sandbox lines from the compact view
    if (e.label?.startsWith("Sandbox:")) continue;
    const key = `${e.type}:${e.label}`;
    if (!byKey.has(key)) order.push(key);
    byKey.set(key, e);
  }
  return order.map((k) => byKey.get(k)!).filter(Boolean);
}

function iconFor(type: AgentEvent["type"], status: AgentEvent["status"]) {
  if (status === "running") return <Loader2 size={13} className="trace-spin" />;
  if (status === "fail") return <XCircle size={13} className="trace-fail" />;
  if (status === "done") return <CheckCircle2 size={13} className="trace-ok" />;
  return <Circle size={13} />;
}

function TraceRow({ evt }: { evt: AgentEvent }) {
  const [open, setOpen] = useState(false);
  const hasDetail = Boolean(evt.detail?.trim());

  return (
    <div className={`trace-row ${evt.status} type-${evt.type}`}>
      <button
        type="button"
        className="trace-row-head"
        onClick={() => hasDetail && setOpen((v) => !v)}
        disabled={!hasDetail}
      >
        <span className="trace-icon">{iconFor(evt.type, evt.status)}</span>
        <span className="trace-label">{evt.label}</span>
        {hasDetail && (open ? <ChevronDown size={12} /> : <ChevronRight size={12} />)}
      </button>
      {open && hasDetail && <pre className="trace-detail">{evt.detail}</pre>}
    </div>
  );
}

export function TracePanel({ events, compact, onCancel }: Props) {
  const collapsed = useMemo(() => collapseEvents(events), [events]);
  if (collapsed.length === 0) return null;

  const running = collapsed.some((e) => e.status === "running");
  // Idle history: don't clog the thread — only show while work is in flight
  if (!running && !compact) return null;

  return (
    <div className={`trace-panel ${compact ? "compact" : ""} ${running ? "live" : ""}`}>
      <div className="trace-panel-head">
        {running ? <Loader2 size={13} className="trace-spin" /> : <CheckCircle2 size={13} className="trace-ok" />}
        <span>{running ? "Working" : "Done"}</span>
        <span className="trace-count">{collapsed.length}</span>
        {running && onCancel && (
          <button type="button" className="trace-stop" onClick={onCancel}>
            Stop
          </button>
        )}
      </div>
      <div className="trace-list">
        {collapsed.map((evt) => (
          <TraceRow key={`${evt.type}:${evt.label}`} evt={evt} />
        ))}
      </div>
    </div>
  );
}
