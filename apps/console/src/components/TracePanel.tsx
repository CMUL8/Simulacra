import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  Loader2,
  Terminal,
  Wrench,
  XCircle,
} from "lucide-react";
import { useState } from "react";
import type { AgentEvent } from "../api";

type Props = {
  events: AgentEvent[];
  compact?: boolean;
  onCancel?: () => void;
};

function iconFor(type: AgentEvent["type"], status: AgentEvent["status"]) {
  if (status === "running") return <Loader2 size={13} className="trace-spin" />;
  if (status === "fail") return <XCircle size={13} className="trace-fail" />;
  if (type === "tool") return <Wrench size={13} />;
  if (type === "gate") return status === "done" ? <CheckCircle2 size={13} className="trace-ok" /> : <XCircle size={13} />;
  if (type === "think") return <Terminal size={13} />;
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
  if (events.length === 0) return null;

  const running = events.some((e) => e.status === "running");

  return (
    <div className={`trace-panel ${compact ? "compact" : ""}`}>
      <div className="trace-panel-head">
        {running ? <Loader2 size={13} className="trace-spin" /> : <Terminal size={13} />}
        <span>Agent activity</span>
        <span className="trace-count">{events.length}</span>
        {running && onCancel && (
          <button type="button" className="trace-stop" onClick={onCancel}>
            Stop
          </button>
        )}
      </div>
      <div className="trace-list">
        {events.map((evt) => (
          <TraceRow key={evt.id} evt={evt} />
        ))}
      </div>
    </div>
  );
}
