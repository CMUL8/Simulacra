import {
  Folder,
  FolderOpen,
  Home,
  MessageSquare,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { ChatThreadSummary, DataRoomFile, Project } from "../api";
import { userFacingFiles } from "../lib/userFacingFiles";
import { FileTypeIcon } from "./FileTypeIcon";
import { humanSourceLabel } from "./agent/AnswerBlock";

type Props = {
  projects: Project[];
  activeId: string | null;
  activeChatId?: string | null;
  busyProjectIds?: Record<string, boolean>;
  files: DataRoomFile[];
  focus: "projects" | "files";
  collapsed: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onSelectChat?: (projectId: string, chatId: string) => void;
  onNewChat?: (projectId: string) => void;
  onDeleteChat?: (projectId: string, chatId: string) => void;
  onToggle: () => void;
  onHome?: () => void;
};

function relativeWhen(iso?: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.max(0, Math.round((Date.now() - t) / 60_000));
  if (mins < 1) return "";
  if (mins < 60) return `${mins}m`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  const days = Math.round(hrs / 24);
  if (days < 14) return `${days}d`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function chatsFor(p: Project): ChatThreadSummary[] {
  const idx = p.chat_index || p.chats;
  if (idx && idx.length) return idx;
  const title = (p.chat?.[0]?.content || p.prompt || "Chat").split("\n")[0]!.slice(0, 48);
  return [
    {
      id: p.active_chat_id || "main",
      title: title || "Chat",
      updated_at: p.created_at || new Date().toISOString(),
      active: true,
      message_count: p.chat?.length || 0,
      artifact_mode: "shared",
    },
  ];
}

function chatLabel(c: ChatThreadSummary, index: number): string {
  const t = (c.title || "").trim();
  if (!t || /^new chat$/i.test(t) || /^chat$/i.test(t) || /^main chat$/i.test(t)) {
    return index === 0 ? "Chat" : `Chat ${index + 1}`;
  }
  return t;
}

/** Cursor-like project → chats rail. No tooltip flash; delete on hover. */
export function Sidebar({
  projects,
  activeId,
  activeChatId,
  busyProjectIds = {},
  files,
  collapsed,
  onNew,
  onSelect,
  onSelectChat,
  onNewChat,
  onDeleteChat,
  onToggle,
  onHome,
}: Props) {
  const [query, setQuery] = useState("");
  const [dataRoomOpen, setDataRoomOpen] = useState(false);

  useEffect(() => {
    // Keep data room collapsed by default — less chrome noise
  }, []);

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
    <aside className="sidebar bui-sidebar">
      <div className="bui-sidebar-top">
        <div className="bui-sidebar-brand">
          <span className="bui-sidebar-mark">S</span>
          <strong>Simulacra</strong>
        </div>
        <div className="bui-sidebar-search">
          <Search size={13} aria-hidden />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search"
            aria-label="Search projects"
          />
        </div>
      </div>

      <div className="sidebar-section grow projects-section">
        <div className="sidebar-head title-case">
          <span>Projects</span>
        </div>
        <ul className="project-list nested">
          {filteredProjects.length === 0 && <li className="empty">No projects</li>}
          {filteredProjects.map((p) => {
            const isActive = activeId === p.id;
            const chats = chatsFor(p);
            const isBusy = Boolean(busyProjectIds[p.id]);
            return (
              <li key={p.id} className={`project-group${isActive ? " on" : ""}${isBusy ? " busy" : ""}`}>
                <div className={`project-row${isActive ? " active" : ""}`}>
                  <button
                    type="button"
                    className="project-item"
                    onClick={() => onSelect(p.id)}
                    title={isBusy ? "Working…" : undefined}
                  >
                    {isActive ? (
                      <FolderOpen size={14} className="project-folder" />
                    ) : (
                      <Folder size={14} className="project-folder" />
                    )}
                    <span className="project-title">{p.app_config?.title || "Untitled"}</span>
                    {isBusy ? <span className="project-activity" aria-label="Working" /> : null}
                  </button>
                  {isActive && onNewChat ? (
                    <button
                      type="button"
                      className="row-action"
                      aria-label="New chat"
                      onClick={(e) => {
                        e.stopPropagation();
                        onNewChat(p.id);
                      }}
                    >
                      <Plus size={13} />
                    </button>
                  ) : null}
                </div>
                {isActive ? (
                  <ul className="chat-list">
                    {chats.map((c, i) => {
                      const chatActive = (activeChatId || p.active_chat_id) === c.id;
                      const canDelete = Boolean(onDeleteChat) && chats.length > 1;
                      return (
                        <li key={c.id} className="chat-row">
                          <button
                            type="button"
                            className={`chat-item${chatActive ? " active" : ""}`}
                            onClick={() => {
                              if (onSelectChat) onSelectChat(p.id, c.id);
                              else onSelect(p.id);
                            }}
                          >
                            <MessageSquare size={12} className="chat-icon" />
                            <span className="chat-title">{chatLabel(c, i)}</span>
                            {isBusy && chatActive ? (
                              <span className="chat-activity" aria-label="Working" />
                            ) : (
                              <span className="chat-age">{relativeWhen(c.updated_at)}</span>
                            )}
                          </button>
                          {canDelete ? (
                            <button
                              type="button"
                              className="row-action danger"
                              aria-label="Delete chat"
                              onClick={(e) => {
                                e.stopPropagation();
                                onDeleteChat?.(p.id, c.id);
                              }}
                            >
                              <Trash2 size={12} />
                            </button>
                          ) : null}
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
          className="sidebar-head title-case collapsible"
          onClick={() => setDataRoomOpen((v) => !v)}
          aria-expanded={dataRoomOpen}
        >
          <span>Data Room</span>
          <span className="badge quiet">{filteredFiles.length}</span>
        </button>
        {dataRoomOpen ? (
          <ul className="file-list quiet" aria-label="Data room files">
            {filteredFiles.map((f) => (
              <li key={f.name} className="file-row" title={f.name}>
                <FileTypeIcon ext={f.type} />
                <span className="file-name">{humanSourceLabel(f.name)}</span>
              </li>
            ))}
            {filteredFiles.length === 0 && <li className="empty">Empty</li>}
          </ul>
        ) : null}
      </div>

      <div className="bui-sidebar-foot">
        {onHome ? (
          <button type="button" className="bui-sidebar-link icon-only" onClick={onHome} aria-label="Home">
            <Home size={15} />
          </button>
        ) : null}
        <button type="button" className="bui-sidebar-new icon-only" onClick={onNew} aria-label="New project">
          <Plus size={16} strokeWidth={2} />
        </button>
      </div>

      <button type="button" className="sidebar-collapse" onClick={onToggle} aria-label="Collapse sidebar">
        ‹
      </button>
    </aside>
  );
}
