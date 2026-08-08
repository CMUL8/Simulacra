import type { Project } from "../api";

type Props = {
  project: Project | null;
  apiOk: boolean;
};

export function StatusBar({ project, apiOk }: Props) {
  const phase = project?.phase === "ready" ? "build" : project?.phase ?? "plan";
  const label = project
    ? `${project.app_config?.title || "Project"} · ${phase}`
    : apiOk
      ? "Ready"
      : "Offline";

  return (
    <footer className="status-bar" title={project?.id}>
      <span className={`status-dot ${apiOk ? "ok" : "off"}`} aria-hidden />
      <span className="status-label">{label}</span>
      {project?.gates_status && project.gates_status !== "pending" && (
        <span className={`status-quiet gate-${project.gates_status}`}>{project.gates_status}</span>
      )}
    </footer>
  );
}
