import {
  ArrowRight,
  Globe,
  PanelLeft,
  PanelLeftClose,
  RotateCcw,
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

function honestyLabel(source?: string | null): { text: string; cls: string } | null {
  if (!source || source === "system") return null;
  if (source === "prime") return { text: "Prime", cls: "source-prime" };
  if (source === "template") return { text: "Template", cls: "source-heuristic" };
  if (source === "cancelled") return { text: "Stopped", cls: "source-error" };
  if (source === "timeout") return { text: "Timed out", cls: "source-error" };
  if (source === "error") return { text: "Fallback", cls: "source-error" };
  if (source === "heuristic") return { text: "Heuristic", cls: "source-heuristic" };
  return null;
}

function ThinkingLoader({ label, onStop }: { label: string; onStop?: () => void }) {
  return (
    <div className="cursor-thinking" aria-live="polite" aria-busy="true">
      <span className="cursor-thinking-dots" aria-hidden>
        <i />
        <i />
        <i />
      </span>
      <span className="cursor-thinking-label">{label}</span>
      {onStop && (
        <button type="button" className="cursor-thinking-stop" onClick={onStop}>
          Stop
        </button>
      )}
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
  const waitingForOpen = busy && !project.chat.some((m) => m.role === "assistant");
  const jobKind = snapshot.job?.kind ?? project.job?.kind;
  const thinkingLabel =
    jobKind === "bootstrap" || (isPlan && waitingForOpen)
      ? "Building your preview…"
      : jobKind === "build_run"
        ? "Prime is customizing…"
        : "Working…";
  const deepenLabel = hasPreview ? "Improve with Prime" : "Approve & Build";
  const lastAssistant = [...project.chat].reverse().find((m) => m.role === "assistant");
  const honesty = honestyLabel(lastAssistant?.source ?? project.prime?.source);
  const showStyleBar = Boolean(designBrief && onSaveDesignBrief);

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
          {honesty && <span className={`source-chip ${honesty.cls}`}>{honesty.text}</span>}
          {project.deployed && <span className="deployed-pill">live</span>}
        </div>
        <div className="agent-topbar-right">
          {hasPreview && (
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
          {onApprove && (
            <button type="button" className="approve-btn" disabled={busy} onClick={onApprove}>
              {deepenLabel}
              <ArrowRight size={14} />
            </button>
          )}
        </div>
      </header>

      {(isPlan || !hasPreview) && (
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

          {busy && (isPlan || traces.length === 0 || waitingForOpen) && (
            <ThinkingLoader label={thinkingLabel} onStop={onCancel} />
          )}

          <div ref={endRef} />
        </div>

        <div className="agent-composer-wrap">
          {showStyleBar && (
            <DesignBriefForm value={designBrief!} onSave={onSaveDesignBrief!} disabled={false} />
          )}
          <PromptComposer
            value={input}
            onChange={onInput}
            onSubmit={onSend}
            onCancel={onCancel}
            disabled={project.status === "failed"}
            busy={busy}
            files={files}
            placeholder={isPlan && !hasPreview ? "Send follow-up" : "Ask for changes…"}
            submitLabel="Send"
            modeTag={hasPreview ? "Maker" : "Plan"}
          />
        </div>
      </div>
    </div>
  );
}
