import {
  Folder,
  FolderOpen,
  MessageSquare,
  Plus,
  Search,
  Settings,
  Trash2,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { AuthUser, ChatThreadSummary, DataRoomFile, Project } from "../api";
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
  user?: AuthUser | null;
  workspaceLabel?: string;
  focusSearch?: boolean;
  onFocusSearchConsumed?: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onSelectChat?: (projectId: string, chatId: string) => void;
  onNewChat?: (projectId: string) => void;
  onDeleteChat?: (projectId: string, chatId: string) => void;
  onToggle: () => void;
  onHome?: () => void;
  onAccount?: () => void;
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

function initials(user?: AuthUser | null): string {
  const raw = (user?.name || user?.email || "?").trim();
  const parts = raw.split(/[\s@._-]+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0]![0] || ""}${parts[1]![0] || ""}`.toUpperCase();
  }
  return raw.slice(0, 2).toUpperCase() || "?";
}

function displayName(user?: AuthUser | null): string {
  const name = user?.name?.trim();
  if (name) return name;
  const email = user?.email?.trim();
  if (email) return email.split("@")[0] || email;
  return "Account";
}

/** Cursor-like project → chats rail. Search is a nav row; identity lives at the bottom. */
export function Sidebar({
  projects,
  activeId,
  activeChatId,
  busyProjectIds = {},
  files,
  collapsed,
  user = null,
  workspaceLabel = "Workspace",
  focusSearch = false,
  onFocusSearchConsumed,
  onNew,
  onSelect,
  onSelectChat,
  onNewChat,
  onDeleteChat,
  onToggle,
  onHome,
  onAccount,
}: Props) {
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [dataRoomOpen, setDataRoomOpen] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!focusSearch) return;
    setSearchOpen(true);
    onFocusSearchConsumed?.();
  }, [focusSearch, onFocusSearchConsumed]);

  useEffect(() => {
    if (!searchOpen) return;
    searchRef.current?.focus();
  }, [searchOpen]);

  useEffect(() => {
    if (!searchOpen) return;
    function onKey(e: KeyboardEvent) {
      if (e.key !== "Escape") return;
      if (query) {
        setQuery("");
        return;
      }
      setSearchOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [searchOpen, query]);

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
        <button
          type="button"
          className="bui-sidebar-brand"
          onClick={onHome}
          aria-label="Home"
        >
          <strong>Simulacra</strong>
        </button>
        <button type="button" className="bui-nav-btn" onClick={onNew}>
          <Plus size={16} strokeWidth={1.5} aria-hidden />
          New project
        </button>
        {searchOpen ? (
          <div className="bui-sidebar-search">
            <Search size={16} strokeWidth={1.5} aria-hidden />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search"
              aria-label="Search projects"
            />
            <button
              type="button"
              className="search-clear"
              aria-label="Close search"
              onClick={() => {
                setQuery("");
                setSearchOpen(false);
              }}
            >
              <X size={14} strokeWidth={1.5} />
            </button>
          </div>
        ) : (
          <button
            type="button"
            className="bui-nav-btn"
            onClick={() => setSearchOpen(true)}
            title="Search (⌘K)"
          >
            <Search size={16} strokeWidth={1.5} aria-hidden />
            Search
          </button>
        )}
      </div>

      <div className="sidebar-section grow projects-section">
        <div className="sidebar-head title-case">
          <span>Projects</span>
        </div>
        <ul className="project-list nested">
          {filteredProjects.length === 0 && <li className="empty">{q ? "No matches" : "No projects"}</li>}
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
                      <FolderOpen size={14} strokeWidth={1.5} className="project-folder" />
                    ) : (
                      <Folder size={14} strokeWidth={1.5} className="project-folder" />
                    )}
                    <span className="project-title">{p.app_config?.title || "Untitled"}</span>
                    {isBusy ? (
                      <span className="project-activity" aria-label="Working in background" title="Working in background" />
                    ) : null}
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
                      <Plus size={14} strokeWidth={1.5} />
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
                            <MessageSquare size={14} strokeWidth={1.5} className="chat-icon" />
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
                              <Trash2 size={14} strokeWidth={1.5} />
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
        <button
          type="button"
          className="bui-identity"
          onClick={onAccount}
          aria-label="Account"
        >
          <span className="bui-avatar" aria-hidden>
            {initials(user)}
          </span>
          <span className="bui-identity-copy">
            <span className="bui-identity-name">{displayName(user)}</span>
            <span className="bui-identity-plan">{workspaceLabel}</span>
          </span>
        </button>
        <button
          type="button"
          className="bui-sidebar-gear"
          onClick={onAccount}
          aria-label="Settings"
        >
          <Settings size={16} strokeWidth={1.5} />
        </button>
      </div>

      <button type="button" className="sidebar-collapse" onClick={onToggle} aria-label="Collapse sidebar">
        ‹
      </button>
    </aside>
  );
}
