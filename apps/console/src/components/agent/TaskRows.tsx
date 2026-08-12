/** Task rows — live agent task status (Beautiful UI 06). */
import { Check, Circle, Loader2, X } from "lucide-react";

export type TaskRowItem = {
  id: string;
  title: string;
  detail?: string;
  status: "running" | "completed" | "failed" | "pending";
};

type Props = {
  tasks: TaskRowItem[];
};

export function TaskRows({ tasks }: Props) {
  if (!tasks.length) return null;
  return (
    <ul className="bui-tasks" aria-label="Tasks">
      {tasks.map((t) => (
        <li key={t.id} className={`bui-task bui-task-${t.status}`}>
          <span className="bui-task-icon" aria-hidden>
            {t.status === "completed" ? (
              <Check size={13} strokeWidth={2.5} />
            ) : t.status === "failed" ? (
              <X size={13} strokeWidth={2.5} />
            ) : t.status === "running" ? (
              <Loader2 size={13} className="spin" />
            ) : (
              <Circle size={11} />
            )}
          </span>
          <span className="bui-task-body">
            <span className="bui-task-title">{t.title}</span>
            {t.detail ? <span className="bui-task-detail">{t.detail}</span> : null}
          </span>
          <span className="bui-task-status">{t.status}</span>
        </li>
      ))}
    </ul>
  );
}

/** Derive task rows from job + recent events for build waits. */
export function tasksFromJob(opts: {
  jobKind?: string | null;
  jobStatus?: string | null;
  phase?: string | null;
  fileCount?: number;
}): TaskRowItem[] {
  const kind = opts.jobKind || "";
  const running = opts.jobStatus === "running" || opts.jobStatus === "settling";
  if (kind === "iterate_run") {
    return [
      { id: "read", title: "Read current preview", status: running ? "running" : "completed" },
      { id: "edit", title: "Apply your changes", status: running ? "running" : "pending" },
      { id: "refresh", title: "Refresh preview", status: "pending" },
    ];
  }
  if (kind === "bootstrap" || kind === "build_run" || kind === "approve_build") {
    return [
      {
        id: "sources",
        title: "Scan sources",
        detail: opts.fileCount ? `${opts.fileCount} files` : undefined,
        status: "completed",
      },
      {
        id: "scaffold",
        title: "Scaffold artifact",
        status: running ? "running" : opts.phase === "ready" ? "completed" : "pending",
      },
      {
        id: "customize",
        title: "Customize with builder",
        status: running && kind === "build_run" ? "running" : "pending",
      },
      { id: "preview", title: "Publish preview", status: "pending" },
    ];
  }
  return [];
}
