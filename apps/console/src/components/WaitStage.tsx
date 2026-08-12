import { useEffect, useMemo, useState } from "react";
import type { AgentEvent } from "../api";
import { PixelLoader } from "./agent/PixelLoader";
import { TaskRows, tasksFromJob } from "./agent/TaskRows";
import { ThinkingTrail, formatThoughtElapsed, tracesForWait } from "./agent/ThinkingTrail";

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
  const feedEvents = useMemo(() => tracesForWait(traces, startedAt), [traces, startedAt]);
  const tasks = useMemo(
    () => tasksFromJob({ jobKind, jobStatus, phase, fileCount, events: feedEvents }),
    [jobKind, jobStatus, phase, fileCount, feedEvents],
  );

  if (chatMode) {
    return (
      <div className="wait-stage wait-stage-chat" role="status" aria-live="polite" aria-busy="true">
        <ThinkingTrail events={traces} live startedAt={startedAt} onStop={onStop} />
      </div>
    );
  }

  const shortTitle = (title || "Building").replace(/\s*…\s*$/, "");

  return (
    <div
      className={`wait-stage wait-stage-${variant} wait-stage-build`}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="bui-build-wait">
        <div className="bui-build-wait-head">
          <PixelLoader label={shortTitle} compact />
          <div className="bui-build-wait-actions">
            <span className="bui-build-wait-elapsed">{formatThoughtElapsed(elapsed)}</span>
            {onStop ? (
              <button type="button" className="bui-thinking-stop" onClick={onStop}>
                Stop
              </button>
            ) : null}
          </div>
        </div>
        {subtitle ? <p className="bui-build-wait-sub">{subtitle}</p> : null}
        <TaskRows tasks={tasks} compact />
      </div>
    </div>
  );
}
