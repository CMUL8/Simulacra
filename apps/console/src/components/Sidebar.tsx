import { CheckCircle2, Clock, Plus, Search, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import type { DataRoomFile, Project } from "../api";
import { FileTypeIcon } from "./FileTypeIcon";
import { Tooltip } from "./ui/Tooltip";

type Props = {
  projects: Project[];
  activeId: string | null;
  files: DataRoomFile[];
  focus: "projects" | "files";
  collapsed: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onToggle: () => void;
};

function fmtSize(n: number) {
  if (n < 1024) return `${n} B`;
  return `${(n / 1024).toFixed(1)} KB`;
}

function StatusIcon({ status, gates }: { status: string; gates: string }) {
  if (gates === "pass" && status === "deployed") return <CheckCircle2 size={12} className="status-icon pass" />;
  if (gates === "fail" || status === "failed") return <XCircle size={12} className="status-icon fail" />;
  return <Clock size={12} className="status-icon pending" />;
}

function projectStatusLabel(p: Project): string {
  if (p.deployed || p.status === "deployed") return "Shipped";
  if (p.phase === "ready" || p.status === "ready") return "Built";
  if (p.phase === "plan" || p.status === "planning" || p.status === "draft") return "Draft";
  const status = (p.status || "").toLowerCase();
  if (["building_app", "publishing_preview", "approved"].includes(status)) return "Building";
  if (["extracting", "gating"].includes(status)) return "Scanning";
  if (status === "failed") return "Failed";
  if (status.includes("_")) return p.phase === "build" ? "Building" : "Draft";
  return p.phase === "build" ? "Building" : "Draft";
}

export function Sidebar({ projects, activeId, files, focus, collapsed, onNew, onSelect, onToggle }: Props) {
  const [filter, setFilter] = useState("");

  const filteredFiles = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return files;
    return files.filter((f) => f.name.toLowerCase().includes(q));
  }, [files, filter]);

  if (collapsed) return null;

  return (
    <aside className={`sidebar ${focus === "files" ? "focus-files" : ""}`}>
      <div className="sidebar-section">
        <div className="sidebar-head">
          <span>Projects</span>
          <Tooltip label="New project">
            <button type="button" className="icon-btn" onClick={onNew}>
              <Plus size={15} strokeWidth={2} />
            </button>
          </Tooltip>
        </div>
        <ul className="project-list">
          {projects.length === 0 && <li className="empty">No projects yet</li>}
          {projects.map((p) => (
            <li key={p.id}>
              <button
                type="button"
                className={`project-item ${activeId === p.id ? "active" : ""}`}
                onClick={() => onSelect(p.id)}
              >
                <StatusIcon status={p.status} gates={p.gates_status} />
                <div className="project-text">
                  <span className="project-title">{p.app_config?.title || "Untitled"}</span>
                  <span className="project-meta">
                    {p.row_count} rows · {projectStatusLabel(p)}
                  </span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="sidebar-section grow">
        <div className="sidebar-head">
          <span>Data room</span>
          <span className="badge">{files.length}</span>
        </div>
        <div className="sidebar-search">
          <Search size={13} className="search-icon" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter files…"
            aria-label="Filter data room files"
          />
        </div>
        <ul className="file-list">
          {filteredFiles.map((f) => (
            <li key={f.name} className="file-item">
              <FileTypeIcon ext={f.type} />
              <span className="file-name" title={f.name}>
                {f.name}
              </span>
              <span className="file-size">{fmtSize(f.size)}</span>
            </li>
          ))}
          {filteredFiles.length === 0 && <li className="empty">No files match</li>}
        </ul>
      </div>

      <button type="button" className="sidebar-collapse" onClick={onToggle} aria-label="Collapse sidebar">
        ‹
      </button>
    </aside>
  );
}
