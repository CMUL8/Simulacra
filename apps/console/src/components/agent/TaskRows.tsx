/** Task rows — live agent task status (Beautiful UI 06). */
import { Check, Circle, Loader2, X } from "lucide-react";
import type { AgentEvent } from "../../api";

export type TaskRowItem = {
  id: string;
  title: string;
  detail?: string;
  status: "running" | "completed" | "failed" | "pending";
};

type Props = {
  tasks: TaskRowItem[];
  compact?: boolean;
};

export function TaskRows({ tasks, compact }: Props) {
  if (!tasks.length) return null;
  return (
    <ul className={`bui-tasks${compact ? " compact" : ""}`} aria-label="Tasks">
      {tasks.map((t) => (
        <li key={t.id} className={`bui-task bui-task-${t.status}`}>
          <span className="bui-task-icon" aria-hidden>
            {t.status === "completed" ? (
              <Check size={12} strokeWidth={2.5} />
            ) : t.status === "failed" ? (
              <X size={12} strokeWidth={2.5} />
            ) : t.status === "running" ? (
              <Loader2 size={12} className="spin" />
            ) : (
              <Circle size={10} />
            )}
          </span>
          <span className="bui-task-body">
            <span className="bui-task-title">
              {t.title}
              {t.detail ? <em> · {t.detail}</em> : null}
            </span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function hasLabel(events: AgentEvent[] | undefined, re: RegExp): boolean {
  if (!events?.length) return false;
  return events.some((e) => re.test((e.label || "").trim()));
}

/** Derive a short task list from job + recent events for build waits. */
export function tasksFromJob(opts: {
  jobKind?: string | null;
  jobStatus?: string | null;
  phase?: string | null;
  fileCount?: number;
  events?: AgentEvent[];
}): TaskRowItem[] {
  const kind = opts.jobKind || "";
  const running = opts.jobStatus === "running" || opts.jobStatus === "settling";
  const ev = opts.events;
  const reading = hasLabel(ev, /read(ing)? sources|scan(ning)? sources/i);
  const building = hasLabel(ev, /build(ing)? (app|report|slides)|scaffold|customiz/i);
  const publishing = hasLabel(ev, /publish|preview|deploy/i);

  if (kind === "iterate_run") {
    return [
      { id: "edit", title: "Updating preview", status: running ? "running" : "completed" },
    ];
  }
  if (kind === "bootstrap" || kind === "build_run" || kind === "approve_build") {
    const sourcesDone = (opts.fileCount || 0) > 0 || reading || building || publishing;
    const scaffoldDone = publishing || opts.phase === "ready";
    const customizeActive = running && (kind === "build_run" || building) && !publishing;
    return [
      {
        id: "sources",
        title: "Sources",
        detail: opts.fileCount ? `${opts.fileCount}` : undefined,
        status: sourcesDone ? "completed" : running ? "running" : "pending",
      },
      {
        id: "build",
        title: "Build",
        status: scaffoldDone
          ? "completed"
          : customizeActive || (running && sourcesDone)
            ? "running"
            : "pending",
      },
      {
        id: "preview",
        title: "Preview",
        status: opts.phase === "ready" ? "completed" : publishing ? "running" : "pending",
      },
    ];
  }
  return [];
}
