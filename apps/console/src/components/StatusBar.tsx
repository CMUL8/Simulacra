import { Circle, Database, GitBranch, Wifi, WifiOff } from "lucide-react";
import type { Project } from "../api";

type Props = {
  project: Project | null;
  apiOk: boolean;
};

export function StatusBar({ project, apiOk }: Props) {
  return (
    <footer className="status-bar">
      <span className="status-item">
        {apiOk ? <Wifi size={11} /> : <WifiOff size={11} />}
        {apiOk ? "Connected" : "Offline"}
      </span>
      <span className="status-item">
        <GitBranch size={11} />
        Prime · {project?.phase === "ready" ? "build" : project?.phase ?? "plan"}
      </span>
      {project && (
        <>
          <span className="status-item mono">{project.id}</span>
          <span className="status-item">
            <Database size={11} />
            {project.row_count} rows
          </span>
          <span className={`status-item gate-${project.gates_status}`}>
            <Circle size={8} fill="currentColor" stroke="none" />
            gates {project.gates_status}
          </span>
          <span className="status-item">{project.status}</span>
        </>
      )}
    </footer>
  );
}
