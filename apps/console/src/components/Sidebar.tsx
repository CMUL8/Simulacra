import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Folder,
  FolderOpen,
  Home,
  MessageSquare,
  Plus,
  Search,
  XCircle,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ChatThreadSummary, DataRoomFile, Project } from "../api";
import { userFacingFiles } from "../lib/userFacingFiles";
import { FileTypeIcon } from "./FileTypeIcon";
import { humanSourceLabel } from "./agent/AnswerBlock";
import { Tooltip } from "./ui/Tooltip";

type Props = {
  projects: Project[];
  activeId: string | null;
  activeChatId?: string | null;
  files: DataRoomFile[];
  focus: "projects" | "files";
  collapsed: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onSelectChat?: (projectId: string, chatId: string) => void;
  onNewChat?: (projectId: string) => void;
  onToggle: () => void;
  onHome?: () => void;
};

function relativeWhen(iso?: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.max(0, Math.round((Date.now() - t) / 60_000));
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  const days = Math.round(hrs / 24);
  if (days < 14) return `${days}d`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function StatusIcon({ status, gates }: { status: string; gates: string }) {
  if (gates === "pass" && status === "deployed") return <CheckCircle2 size={12} className="status-icon pass" />;
  if (gates === "fail" || status === "failed") return <XCircle size={12} className="status-icon fail" />;
  return <Clock size={12} className="status-icon pending" />;
}

function statusTitle(p: Project): string {
  if (p.deployed || p.status === "deployed") return "Shipped";
  if (p.phase === "ready" || p.status === "ready") return "Built";
  if (p.phase === "plan" || p.status === "planning" || p.status === "draft") return "Draft";
  const status = (p.status || "").toLowerCase();
  if (["building_app", "publishing_preview", "approved"].includes(status)) return "Building";
  if (["extracting", "gating"].includes(status)) return "Scanning";
  if (status === "failed") return "Failed";
  return p.phase === "build" ? "Building" : "Draft";
}

function chatsFor(p: Project): ChatThreadSummary[] {
  const idx = p.chat_index || p.chats;
  if (idx && idx.length) return idx;
  const title = (p.chat?.[0]?.content || p.prompt || "Main chat").split("\n")[0]!.slice(0, 56);
  return [
    {
      id: p.active_chat_id || "main",
      title: title || "Main chat",
      updated_at: p.created_at || new Date().toISOString(),
      active: true,
      message_count: p.chat?.length || 0,
      artifact_mode: "shared",
    },
  ];
}

function shortFileLabel(name: string): string {
  const label = humanSourceLabel(name);
  const first = label.split(/\s+/)[0] || label;
  return first.length > 14 ? `${first.slice(0, 13)}…` : first;
}

/** Cursor-like sidebar: section actions, lean rows, icon-first data room. */
export function Sidebar({
  projects,
  activeId,
  activeChatId,
  files,
  focus,
  collapsed,
  onNew,
  onSelect,
  onSelectChat,
  onNewChat,
  onToggle,
  onHome,
}: Props) {
  const [query, setQuery] = useState("");
  const [dataRoomOpen, setDataRoomOpen] = useState(true);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!activeId) return;
    setExpanded((prev) => (prev[activeId] ? prev : { ...prev, [activeId]: true }));
  }, [activeId]);

  const visibleFiles = useMemo(() => userFacingFiles(files), [files]);
  const q = query.trim().toLowerCase();
  const filteredProjects = useMemo(() => {
    if (!q) return projects;
    return projects.filter((p) => {
      const title = (p.app_config?.title || "").toLowerCase();
      const prompt = (p.prompt || "").toLowerCase();
      const chatHit = chatsFor(p).some((c) => c.title.toLowerCase().includes(q));
      return title.includes(q) || prompt.includes(q) || p.id.toLowerCase().includes(q) || chatHit;
    });
  }, [projects, q]);

  const filteredFiles = useMemo(() => {
    if (!q) return visibleFiles;
    return visibleFiles.filter(
      (f) => f.name.toLowerCase().includes(q) || humanSourceLabel(f.name).toLowerCase().includes(q),
    );
  }, [visibleFiles, q]);

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
            placeholder="Search…"
            aria-label="Search projects and sources"
          />
        </div>
      </div>

      <div className="sidebar-section grow projects-section">
        <div className="sidebar-head title-case with-actions">
          <span>Projects</span>
          <div className="sidebar-head-actions">
            <span className="badge quiet">{filteredProjects.length}</span>
            {onNewChat && activeId ? (
              <Tooltip label="New chat">
                <button
                  type="button"
                  className="sidebar-head-btn"
                  onClick={() => onNewChat(activeId)}
                  aria-label="New chat in active project"
                >
                  <Plus size={14} />
                </button>
              </Tooltip>
            ) : null}
          </div>
        </div>
        <ul className="project-list nested">
          {filteredProjects.length === 0 && <li className="empty">No projects yet</li>}
          {filteredProjects.map((p) => {
            const open = Boolean(expanded[p.id]) || p.id === activeId;
            const chats = chatsFor(p);
            const isActiveProject = activeId === p.id;
            const st = statusTitle(p);
            return (
              <li key={p.id} className={`project-group${isActiveProject ? " on" : ""}`}>
                <div className="project-row">
                  <button
                    type="button"
                    className="project-twist"
                    aria-label={open ? "Collapse chats" : "Expand chats"}
                    onClick={() => setExpanded((prev) => ({ ...prev, [p.id]: !open }))}
                  >
                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </button>
                  <button
                    type="button"
                    className={`project-item ${isActiveProject ? "active" : ""}`}
                    onClick={() => {
                      setExpanded((prev) => ({ ...prev, [p.id]: true }));
                      onSelect(p.id);
                    }}
                    title={`${p.app_config?.title || "Untitled"} · ${st}`}
                  >
                    {open ? (
                      <FolderOpen size={14} className="project-folder" />
                    ) : (
                      <Folder size={14} className="project-folder" />
                    )}
                    <span className="project-title">{p.app_config?.title || "Untitled"}</span>
                    <span className="project-status" title={st} aria-label={st}>
                      <StatusIcon status={p.status} gates={p.gates_status} />
                    </span>
                  </button>
                </div>
                {open ? (
                  <ul className="chat-list">
                    {chats.map((c) => {
                      const chatActive = isActiveProject && (activeChatId || p.active_chat_id) === c.id;
                      return (
                        <li key={c.id}>
                          <button
                            type="button"
                            className={`chat-item${chatActive ? " active" : ""}`}
                            onClick={() => {
                              if (onSelectChat) onSelectChat(p.id, c.id);
                              else onSelect(p.id);
                            }}
                            title={c.title}
                          >
                            <MessageSquare size={12} className="chat-icon" />
                            <span className="chat-title">{c.title}</span>
                            <span className="chat-age">{relativeWhen(c.updated_at)}</span>
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </div>

      <div className={`sidebar-section data-room-section${dataRoomOpen ? "" : " collapsed"}`}>
        <button
          type="button"
          className="sidebar-head title-case collapsible with-actions"
          onClick={() => setDataRoomOpen((v) => !v)}
          aria-expanded={dataRoomOpen}
        >
          <span>
            {dataRoomOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
            Data Room
          </span>
          <span className="badge quiet">{filteredFiles.length}</span>
        </button>
        {dataRoomOpen ? (
          <ul className="file-list compact-icons" aria-label="Data room files">
            {filteredFiles.map((f) => (
              <li key={f.name}>
                <Tooltip label={`${humanSourceLabel(f.name)} · ${(f.size / 1024).toFixed(1)} KB`}>
                  <button type="button" className="file-chip" title={f.name}>
                    <FileTypeIcon ext={f.type} />
                    <span>{shortFileLabel(f.name)}</span>
                  </button>
                </Tooltip>
              </li>
            ))}
            {filteredFiles.length === 0 && <li className="empty">{q ? "No matches" : "Empty"}</li>}
          </ul>
        ) : null}
      </div>

      <div className="bui-sidebar-foot">
        {onHome ? (
          <Tooltip label="Home">
            <button type="button" className="bui-sidebar-link icon-only" onClick={onHome} aria-label="Home">
              <Home size={15} />
            </button>
          </Tooltip>
        ) : null}
        <Tooltip label="New project">
          <button type="button" className="bui-sidebar-new icon-only" onClick={onNew} aria-label="New project">
            <Plus size={16} strokeWidth={2} />
          </button>
        </Tooltip>
      </div>

      <button type="button" className="sidebar-collapse" onClick={onToggle} aria-label="Collapse sidebar">
        ‹
      </button>
    </aside>
  );
}
