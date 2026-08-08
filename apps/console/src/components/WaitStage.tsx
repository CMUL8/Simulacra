import { useEffect, useMemo, useState } from "react";
import type { AgentEvent } from "../api";

type Props = {
  title: string;
  subtitle?: string;
  jobKind?: string | null;
  traces?: AgentEvent[];
  startedAt?: number | null;
  onStop?: () => void;
  /** landing overlay vs thread inline */
  variant?: "thread" | "overlay";
};

const CREATE_STAGES = [
  "Scanning sources",
  "Crafting scaffold",
  "Builder customizing",
  "Publishing preview",
];
const ITERATE_STAGES = ["Reading your note", "Editing the artifact", "Refreshing preview"];
const TIPS = [
  "Good decks take a minute — we’re writing real UI, not a mock.",
  "Scaffold stays behind the scenes; you only see the Built result.",
  "You can Stop anytime — last good preview is kept.",
  "After this, chat drives every change.",
];

function stagesFor(jobKind?: string | null): string[] {
  if (jobKind === "iterate_run") return ITERATE_STAGES;
  if (jobKind === "reingest") return ["Re-ingesting sources", "Refreshing data", "Updating preview"];
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
  // Any running phase advances at least to mid
  if (traces.some((e) => e.status === "running")) return Math.min(stages.length - 1, 2);
  return Math.min(stages.length - 1, 1);
}

function formatElapsed(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m <= 0) return `${s}s`;
  return `${m}:${String(s).padStart(2, "0")}`;
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

  const stages = useMemo(() => stagesFor(jobKind), [jobKind]);
  const active = inferStageIndex(stages, traces);
  const elapsed = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  const tip = TIPS[Math.floor(elapsed / 18) % TIPS.length]!;
  const progress = ((active + 0.55) / stages.length) * 100;

  return (
    <div
      className={`wait-stage wait-stage-${variant}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="wait-stage-card">
        <div className="wait-stage-orbit" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <div className="wait-stage-copy">
          <h3>{title}</h3>
          {subtitle && <p className="wait-stage-sub">{subtitle}</p>}
          <div className="wait-stage-meta">
            <span className="wait-elapsed">{formatElapsed(elapsed)}</span>
            {onStop && (
              <button type="button" className="wait-stop" onClick={onStop}>
                Stop
              </button>
            )}
          </div>
        </div>

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
      </div>
    </div>
  );
}
