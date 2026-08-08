import {
  ArrowRight,
  Globe,
  PanelLeft,
  PanelLeftClose,
  RotateCcw,
  Square,
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

function ThinkingLoader({ label }: { label: string }) {
  return (
    <div className="cursor-thinking" aria-live="polite" aria-busy="true">
      <span className="cursor-thinking-dots" aria-hidden>
        <i />
        <i />
        <i />
      </span>
      <span className="cursor-thinking-label">{label}</span>
    </div>
  );
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
  const waitingForOpen = isPlan && busy && project.chat.length === 0;

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
          <span className="product">
            Simu<em>lacra</em>
          </span>
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
            <button type="button" className="ghost-btn quiet" onClick={onOpenPreview}>
              <Globe size={14} />
              Preview
            </button>
          )}
          {!isPlan && project.checkpoints?.length > 0 && onRollback && (
            <button type="button" className="icon-btn" disabled={busy} onClick={onRollback} title="Rollback">
              <RotateCcw size={14} />
            </button>
          )}
          <button type="button" className="topbar-link" onClick={onGovernance} title="Account, policy & admin">
            Account
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
        <p className="policy-whisper">
          Sources stay behind Simulacra’s control layer — apps never talk to systems directly.
        </p>
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
        <div className="agent-thread cursor-thread">
          {project.chat.map((m: ChatMessage, i: number) => (
            <article key={i} className={`cursor-msg ${m.role}`}>
              <div className="cursor-msg-body">{renderMarkdownLite(m.content)}</div>
            </article>
          ))}

          {!isPlan && traces.length > 0 && (
            <TracePanel events={traces} onCancel={busy ? onCancel : undefined} />
          )}

          {busy && (isPlan || traces.length === 0) && (
            <ThinkingLoader label={waitingForOpen ? "Planning with Prime…" : "Thinking…"} />
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
            placeholder={isPlan ? "Send follow-up" : "Ask for changes…"}
            submitLabel="Send"
            modeTag={isPlan ? "Plan" : "Agent"}
          />
        </div>
      </div>
    </div>
  );
}
