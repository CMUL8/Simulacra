import { useEffect, useMemo, useState } from "react";
import type { AgentEvent } from "../api";
import { PixelLoader } from "./agent/PixelLoader";
import { TaskRows, tasksFromJob } from "./agent/TaskRows";
import { ThinkingTrail, formatThoughtElapsed, tracesForWait } from "./agent/ThinkingTrail";
import { ToolChips } from "./agent/ToolChips";
import { ActivityFeed } from "./ActivityFeed";

type Props = {
  title: string;
  subtitle?: string;
  jobKind?: string | null;
  jobStatus?: string | null;
  phase?: string | null;
  fileCount?: number;
  traces?: AgentEvent[];
  startedAt?: number | null;
  onStop?: () => void;
  variant?: "thread" | "overlay";
};

const TIPS = [
  "Usually under a minute — writing real UI.",
  "Stop anytime; the last good preview stays.",
  "After this, chat drives every change.",
];

function isChatJob(jobKind?: string | null): boolean {
  return !jobKind || jobKind === "agent_chat" || jobKind === "plan_ask";
}

export function WaitStage({
  title,
  subtitle,
  jobKind,
  jobStatus,
  phase,
  fileCount,
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
  const elapsed = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  const tip = TIPS[Math.floor(elapsed / 18) % TIPS.length]!;
  const feedEvents = useMemo(() => tracesForWait(traces, startedAt), [traces, startedAt]);
  const tasks = useMemo(
    () => tasksFromJob({ jobKind, jobStatus, phase, fileCount }),
    [jobKind, jobStatus, phase, fileCount],
  );

  if (chatMode) {
    return (
      <div className="wait-stage wait-stage-chat" role="status" aria-live="polite" aria-busy="true">
        <div className="bui-wait-chat-head">
          <PixelLoader label="Churning" startedAt={startedAt} compact />
        </div>
        <ThinkingTrail events={traces} live startedAt={startedAt} onStop={onStop} />
        <ToolChips events={feedEvents} />
      </div>
    );
  }

  return (
    <div
      className={`wait-stage wait-stage-${variant}`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="wait-stage-card bui-wait-card">
        <div className="wait-stage-top">
          <PixelLoader label={title || "Building"} startedAt={startedAt} />
          <div className="wait-stage-meta">
            <span className="wait-elapsed">{formatThoughtElapsed(elapsed)}</span>
            {onStop && (
              <button type="button" className="wait-stop" onClick={onStop}>
                Stop
              </button>
            )}
          </div>
        </div>
        {subtitle ? <p className="wait-stage-sub">{subtitle}</p> : null}
        <TaskRows tasks={tasks} />
        <ToolChips events={feedEvents} limit={5} />
        <p className="wait-tip">{tip}</p>
        <ActivityFeed events={feedEvents} live limit={4} />
      </div>
    </div>
  );
}
