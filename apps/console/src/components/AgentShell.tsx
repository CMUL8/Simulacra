import {
  ArrowRight,
  Bot,
  Globe,
  PanelLeft,
  PanelLeftClose,
  RotateCcw,
  Shield,
  Square,
  User,
} from "lucide-react";
import { useEffect, useRef } from "react";
import type { AgentEvent, ChatMessage, DataRoomFile, DesignBrief, Snapshot } from "../api";
import { DesignBriefForm } from "./DesignBriefForm";
import { PromptComposer } from "./PromptComposer";
import { TracePanel } from "./TracePanel";

type Props = {
  variant: "plan" | "workspace";
  snapshot: Snapshot;
  files: DataRoomFile[];
  input: string;
  busy: boolean;
  error: string | null;
  traces: AgentEvent[];
  sidebarOpen: boolean;
  designBrief?: DesignBrief;
  onSaveDesignBrief?: (v: DesignBrief) => Promise<void>;
  onToggleSidebar: () => void;
  onInput: (v: string) => void;
  onSend: () => void;
  onApprove?: () => void;
  onCancel?: () => void;
  onOpenPreview: () => void;
  onGovernance: () => void;
  onRollback?: () => void;
  onDismissError: () => void;
  onNew?: () => void;
};

function formatTime(at?: string) {
  if (!at) return "";
  try {
    return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function renderMarkdownLite(text: string) {
  return text.split("\n").map((line, i) => {
    let html = line
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    if (html.startsWith("- ")) {
      html = `<span class="md-li">${html.slice(2)}</span>`;
    }
    return <p key={i} dangerouslySetInnerHTML={{ __html: html || "&nbsp;" }} />;
  });
}

function sourceChip(source?: string | null) {
  if (!source || source === "system") return null;
  const label =
    source === "prime"
      ? "Prime"
      : source === "heuristic"
        ? "Heuristic"
        : source === "error"
          ? "Fallback"
          : source;
  return <span className={`source-chip source-${source}`}>{label}</span>;
}

export function AgentShell({
  variant,
  snapshot,
  files,
  input,
  busy,
  error,
  traces,
  sidebarOpen,
  designBrief,
  onSaveDesignBrief,
  onToggleSidebar,
  onInput,
  onSend,
  onApprove,
  onCancel,
  onOpenPreview,
  onGovernance,
  onRollback,
  onDismissError,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const project = snapshot.project;
  const isPlan = variant === "plan";
  const hasPreview = Boolean(snapshot.preview_url);
  const primeSource = project.prime?.source;

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [project.chat, traces, busy]);

  return (
    <div className="agent-shell">
      <header className="agent-topbar">
        <div className="agent-topbar-left">
          <button type="button" className="icon-btn" onClick={onToggleSidebar} title="Toggle sidebar">
            {sidebarOpen ? <PanelLeftClose size={15} /> : <PanelLeft size={15} />}
          </button>
          <span className="product">Simu<em>lacra</em></span>
          {isPlan && <span className="plan-badge">Planning</span>}
          {primeSource && primeSource !== "none" && sourceChip(primeSource)}
          <span className="sep">/</span>
          <span className="project-name">{project.app_config.title}</span>
          {project.deployed && <span className="deployed-pill">live</span>}
        </div>
        <div className="agent-topbar-right">
          {busy && onCancel && (
            <button type="button" className="stop-btn" onClick={onCancel} title="Stop Prime job">
              <Square size={12} fill="currentColor" />
              Stop
            </button>
          )}
          {!isPlan && hasPreview && (
            <button type="button" className="ghost-btn" onClick={onOpenPreview}>
              <Globe size={14} />
              Preview
            </button>
          )}
          {!isPlan && project.checkpoints?.length > 0 && onRollback && (
            <button type="button" className="icon-btn" disabled={busy} onClick={onRollback} title="Rollback">
              <RotateCcw size={14} />
            </button>
          )}
          <button type="button" className="ghost-btn" onClick={onGovernance}>
            <Shield size={13} />
            Governance
          </button>
          {isPlan && onApprove && (
            <button type="button" className="approve-btn" disabled={busy} onClick={onApprove}>
              Approve & Build
              <ArrowRight size={14} />
            </button>
          )}
        </div>
      </header>

      {isPlan && (
        <div className="integration-banner slim">
          <Shield size={14} />
          <span>
            <strong>Your data stays behind the control layer.</strong> Apps talk to Simulacra — never your systems directly.
          </span>
        </div>
      )}

      {error && (
        <div className="toast error-toast agent-toast" role="alert">
          <span>{error}</span>
          <button type="button" onClick={onDismissError}>
            ×
          </button>
        </div>
      )}

      <div className="agent-center">
        <div className="agent-thread">
          {project.chat.map((m: ChatMessage, i: number) => (
            <article key={i} className={`agent-msg ${m.role}`}>
              <div className="agent-msg-head">
                <span className="avatar">{m.role === "user" ? <User size={12} /> : <Bot size={12} />}</span>
                <span className="who">{m.role === "user" ? "You" : "Simulacra"}</span>
                {m.role === "assistant" && sourceChip(m.source)}
                <time>{formatTime(m.at)}</time>
              </div>
              <div className="agent-msg-body">{renderMarkdownLite(m.content)}</div>
            </article>
          ))}

          {traces.length > 0 && <TracePanel events={traces} onCancel={busy ? onCancel : undefined} />}

          {busy && traces.length === 0 && (
            <article className="agent-msg assistant">
              <div className="agent-msg-head">
                <span className="avatar">
                  <Bot size={12} />
                </span>
                <span className="who">Simulacra</span>
                <span className="source-chip source-prime">Prime</span>
              </div>
              <div className="agent-msg-body dim">
                {isPlan ? "Prime is planning from your request…" : "Working…"}
              </div>
            </article>
          )}

          <div ref={endRef} />
        </div>

        <div className="agent-composer-wrap">
          {isPlan && designBrief && onSaveDesignBrief && (
            <DesignBriefForm value={designBrief} onSave={onSaveDesignBrief} disabled={busy} />
          )}
          <PromptComposer
            value={input}
            onChange={onInput}
            onSubmit={onSend}
            disabled={project.status === "failed"}
            busy={busy}
            files={files}
            placeholder={
              isPlan
                ? "Refine the idea, tag sources with @…"
                : "Ask for changes to your app…"
            }
            submitLabel="Send"
            modeTag={isPlan ? "Plan" : "Agent"}
          />
        </div>
      </div>
    </div>
  );
}
