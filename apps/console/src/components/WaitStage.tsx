import { useEffect, useMemo, useState } from "react";
import type { AgentEvent } from "../api";
import { ActivityFeed } from "./ActivityFeed";

type Props = {
  title: string;
  subtitle?: string;
  jobKind?: string | null;
  traces?: AgentEvent[];
  startedAt?: number | null;
  onStop?: () => void;
  /** thread inline (default). overlay kept for compat — no dark backdrop. */
  variant?: "thread" | "overlay";
};

const CREATE_STAGES = ["Sources", "Scaffold", "Customize", "Preview"];
const ITERATE_STAGES = ["Read", "Edit", "Refresh"];
const TIPS = [
  "Usually under a minute — writing real UI.",
  "Stop anytime; the last good preview stays.",
  "After this, chat drives every change.",
];

function isChatJob(jobKind?: string | null): boolean {
  return !jobKind || jobKind === "agent_chat" || jobKind === "plan_ask";
}

function stagesFor(jobKind?: string | null): string[] {
  if (jobKind === "iterate_run") return ITERATE_STAGES;
  if (jobKind === "reingest") return ["Re-ingest", "Refresh", "Preview"];
  return CREATE_STAGES;
}

function inferStageIndex(stages: string[], traces: AgentEvent[]): number {
  const labels = traces
    .filter((e) => e.type === "phase" || e.type === "gate")
    .map((e) => (e.label || "").toLowerCase());
  if (!labels.length) return 0;
  const latest = labels[labels.length - 1] || "";
  if (latest.includes("publish") || latest.includes("preview")) return Math.min(stages.length - 1, 3);
  if (latest.includes("building") || latest.includes("customiz") || latest.includes("builder"))
    return Math.min(stages.length - 1, 2);
  if (latest.includes("craft") || latest.includes("scaffold") || latest.includes("preparing"))
    return Math.min(stages.length - 1, 1);
  if (latest.includes("scan") || latest.includes("extract") || latest.includes("gate")) return 0;
  if (traces.some((e) => e.status === "running")) return Math.min(stages.length - 1, 2);
  return Math.min(stages.length - 1, 1);
}

function formatElapsed(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m <= 0) return `${s}s`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

/** Events since wait started (or last ~40 if no clock). */
function tracesForWait(traces: AgentEvent[], startedAt?: number | null): AgentEvent[] {
  if (!startedAt) return traces.slice(-40);
  const since = new Date(startedAt - 1500).toISOString();
  const filtered = traces.filter((e) => !e.ts || e.ts >= since);
  return filtered.length ? filtered : traces.slice(-40);
}

export function WaitStage({
  title,
  subtitle,
  jobKind,
  traces = [],
  startedAt,
  onStop,
  variant = "thread",
}: Props) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const chatMode = isChatJob(jobKind);
  const stages = useMemo(() => stagesFor(jobKind), [jobKind]);
  const active = inferStageIndex(stages, traces);
  const elapsed = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  const tip = TIPS[Math.floor(elapsed / 18) % TIPS.length]!;
  const progress = ((active + 0.55) / stages.length) * 100;
  const feedEvents = useMemo(() => tracesForWait(traces, startedAt), [traces, startedAt]);

  return (
    <div
      className={`wait-stage wait-stage-${variant}${chatMode ? " wait-stage-chat" : ""}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="wait-stage-card">
        <div className="wait-stage-top">
          <div className="wait-stage-copy">
            <div className="wait-stage-orbit" aria-hidden>
              <span />
              <span />
              <span />
            </div>
            <div>
              <h3>{title}</h3>
              {subtitle && <p className="wait-stage-sub">{subtitle}</p>}
            </div>
          </div>
          <div className="wait-stage-meta">
            <span className="wait-elapsed">{formatElapsed(elapsed)}</span>
            {onStop && (
              <button type="button" className="wait-stop" onClick={onStop}>
                Stop
              </button>
            )}
          </div>
        </div>

        {!chatMode && (
          <>
            <div className="wait-bar" aria-hidden>
              <i style={{ width: `${Math.min(96, progress)}%` }} />
            </div>
            <ol className="wait-stages">
              {stages.map((label, i) => {
                const state = i < active ? "done" : i === active ? "active" : "todo";
                return (
                  <li key={label} className={`wait-step ${state}`}>
                    <span className="wait-dot" aria-hidden />
                    <span>{label}</span>
                  </li>
                );
              })}
            </ol>
            <p className="wait-tip">{tip}</p>
          </>
        )}

        <ActivityFeed events={feedEvents} live limit={chatMode ? 6 : 5} />
      </div>
    </div>
  );
}
