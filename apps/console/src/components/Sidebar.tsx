import { CheckCircle2, Clock, FolderOpen, Home, Plus, Search, XCircle } from "lucide-react";
import { useMemo, useState } from "react";
import type { DataRoomFile, Project } from "../api";
import { FileTypeIcon } from "./FileTypeIcon";
import { humanSourceLabel } from "./agent/AnswerBlock";
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
  onHome?: () => void;
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

/** Sidebar nav with workspace quick search (Beautiful UI 14). */
export function Sidebar({
  projects,
  activeId,
  files,
  focus,
  collapsed,
  onNew,
  onSelect,
  onToggle,
  onHome,
}: Props) {
  const [query, setQuery] = useState("");

  const q = query.trim().toLowerCase();
  const filteredProjects = useMemo(() => {
    if (!q) return projects;
    return projects.filter((p) => {
      const title = (p.app_config?.title || "").toLowerCase();
      const prompt = (p.prompt || "").toLowerCase();
      return title.includes(q) || prompt.includes(q) || p.id.toLowerCase().includes(q);
    });
  }, [projects, q]);

  const filteredFiles = useMemo(() => {
    if (!q) return files;
    return files.filter((f) => f.name.toLowerCase().includes(q) || humanSourceLabel(f.name).toLowerCase().includes(q));
  }, [files, q]);

  if (collapsed) return null;

  return (
    <aside className={`sidebar bui-sidebar ${focus === "files" ? "focus-files" : ""}`}>
      <div className="bui-sidebar-top">
        <div className="bui-sidebar-brand">
          <span className="bui-sidebar-mark">S</span>
          <div>
            <strong>Simulacra</strong>
            <em>Workspace</em>
          </div>
        </div>
        <div className="bui-sidebar-search">
          <Search size={13} aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Quick search…"
            aria-label="Search projects and sources"
          />
        </div>
        <div className="bui-sidebar-actions">
          {onHome ? (
            <button type="button" className="bui-sidebar-link" onClick={onHome}>
              <Home size={14} />
              Home
            </button>
          ) : null}
          <Tooltip label="New project">
            <button type="button" className="bui-sidebar-new" onClick={onNew}>
              <Plus size={14} strokeWidth={2} />
              New
            </button>
          </Tooltip>
        </div>
      </div>

      <div className="sidebar-section">
        <div className="sidebar-head">
          <span>Projects</span>
          <span className="badge">{filteredProjects.length}</span>
        </div>
        <ul className="project-list">
          {filteredProjects.length === 0 && <li className="empty">No projects yet</li>}
          {filteredProjects.map((p) => (
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
                    {projectStatusLabel(p)}
                    {p.row_count ? ` · ${p.row_count} rows` : ""}
                  </span>
                </div>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="sidebar-section grow">
        <div className="sidebar-head">
          <span>
            <FolderOpen size={12} style={{ marginRight: 6, opacity: 0.7 }} />
            Data room
          </span>
          <span className="badge">{files.length}</span>
        </div>
        <ul className="file-list">
          {filteredFiles.map((f) => (
            <li key={f.name} className="file-item">
              <FileTypeIcon ext={f.type} />
              <span className="file-name" title={f.name}>
                {humanSourceLabel(f.name)}
              </span>
              <span className="file-size">{fmtSize(f.size)}</span>
            </li>
          ))}
          {filteredFiles.length === 0 && <li className="empty">{q ? "No matches" : "No files yet"}</li>}
        </ul>
      </div>

      <button type="button" className="sidebar-collapse" onClick={onToggle} aria-label="Collapse sidebar">
        ‹
      </button>
    </aside>
  );
}
