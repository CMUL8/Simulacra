import {
  ArrowRight,
  Globe,
  PanelLeft,
  PanelLeftClose,
  RotateCcw,
} from "lucide-react";
import { Fragment, type ReactNode, useEffect, useRef } from "react";
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

type TurnKind = "user" | "assistant" | "status" | "plan";

function escapeHtml(s: string) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function inlineFormat(text: string) {
  let html = escapeHtml(text);
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
  return html;
}

/** Document-style markdown — paragraphs, headings, lists (Cursor transcript feel). */
function MarkdownBody({ text }: { text: string }) {
  const blocks = text.replace(/\r\n/g, "\n").trim().split(/\n{2,}/);
  const nodes: ReactNode[] = [];

  blocks.forEach((block, bi) => {
    const lines = block.split("\n");
    const isList = lines.every((l) => !l.trim() || /^[-*]\s+/.test(l.trim()) || /^\d+\.\s+/.test(l.trim()));
    if (isList && lines.some((l) => /^[-*]\s+/.test(l.trim()))) {
      nodes.push(
        <ul key={`ul-${bi}`} className="md-ul">
          {lines
            .filter((l) => l.trim())
            .map((l, li) => (
              <li key={li} dangerouslySetInnerHTML={{ __html: inlineFormat(l.replace(/^[-*]\s+/, "")) }} />
            ))}
        </ul>,
      );
      return;
    }

    lines.forEach((line, li) => {
      const t = line.trim();
      if (!t) return;
      if (t.startsWith("## ")) {
        nodes.push(
          <h3 key={`h-${bi}-${li}`} className="md-h" dangerouslySetInnerHTML={{ __html: inlineFormat(t.slice(3)) }} />,
        );
      } else if (t.startsWith("# ")) {
        nodes.push(
          <h2 key={`h-${bi}-${li}`} className="md-h md-h1" dangerouslySetInnerHTML={{ __html: inlineFormat(t.slice(2)) }} />,
        );
      } else if (t.startsWith("**") && t.endsWith("**") && t.indexOf("**", 2) === t.length - 2) {
        nodes.push(
          <p key={`p-${bi}-${li}`} className="md-lead" dangerouslySetInnerHTML={{ __html: inlineFormat(t) }} />,
        );
      } else {
        nodes.push(
          <p key={`p-${bi}-${li}`} dangerouslySetInnerHTML={{ __html: inlineFormat(t) }} />,
        );
      }
    });
  });

  return <div className="cursor-prose">{nodes}</div>;
}

function turnKind(m: ChatMessage): TurnKind {
  if (m.role === "user") return "user";
  if (m.source === "system") return "status";
  if (m.role === "assistant" && /^##\s*Plan:/i.test(m.content.trim())) return "plan";
  return "assistant";
}

function roleLabel(kind: TurnKind): string | null {
  if (kind === "user") return "You";
  if (kind === "assistant" || kind === "plan") return "Simulacra";
  return null;
}

function stageLabel(source?: string | null): { text: string; cls: string } | null {
  if (!source || source === "system") return null;
  if (source === "prime") return { text: "Built", cls: "source-prime" };
  if (source === "template" || source === "heuristic") return { text: "Draft", cls: "source-heuristic" };
  if (source === "cancelled") return { text: "Stopped", cls: "source-error" };
  if (source === "timeout" || source === "error") return { text: "Retry", cls: "source-error" };
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

function PlanSection({
  snapshot,
  onOpenPreview,
  compact,
}: {
  snapshot: Snapshot;
  onOpenPreview: () => void;
  compact?: boolean;
}) {
  const p = snapshot.project;
  const preview = p.plan_preview;
  const hasPreview = Boolean(snapshot.preview_url);
  const rows = preview?.row_count ?? p.row_count ?? 0;
  const high = preview?.high_risk ?? 0;
  const vendors = preview?.vendors?.length ?? 0;
  const files = (preview?.files ?? []).slice(0, 5);

  return (
    <div className={`plan-section ${compact ? "compact" : ""}`}>
      {!compact && (
        <div className="plan-section-head">
          <h2>{p.app_config?.title || "Your app"}</h2>
          {p.app_config?.subtitle && <p className="plan-section-sub">{p.app_config.subtitle}</p>}
        </div>
      )}
      <div className="plan-section-meta">
        <span>
          {rows} rows
          {high ? ` · ${high} high risk` : ""}
          {vendors ? ` · ${vendors} vendors` : ""}
        </span>
        {files.length > 0 && <span className="plan-section-files">{files.map((f) => f.name).join(" · ")}</span>}
      </div>
      <div className="plan-section-actions">
        <button type="button" className="plan-preview-btn" disabled={!hasPreview} onClick={onOpenPreview}>
          <Globe size={14} />
          {hasPreview ? "Open draft preview" : "Preparing draft…"}
        </button>
        <span className="plan-section-hint">Then Build app when ready</span>
      </div>
    </div>
  );
}

function MessageTurn({
  message,
  snapshot,
  onOpenPreview,
}: {
  message: ChatMessage;
  snapshot: Snapshot;
  onOpenPreview: () => void;
}) {
  const kind = turnKind(message);
  const label = roleLabel(kind);

  return (
    <article className={`cursor-turn cursor-turn-${kind}`}>
      {label && <div className="cursor-turn-role">{label}</div>}
      {kind === "status" ? (
        <div className="cursor-status-line">{message.content.replace(/\*\*/g, "")}</div>
      ) : kind === "plan" ? (
        <Fragment>
          <MarkdownBody text={message.content} />
          <PlanSection snapshot={snapshot} onOpenPreview={onOpenPreview} compact />
        </Fragment>
      ) : (
        <MarkdownBody text={message.content} />
      )}
    </article>
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
      ? "Preparing plan & draft…"
      : jobKind === "build_run"
        ? "Building app…"
        : "Working…";
  const ctaLabel = isPlan ? "Build app" : hasPreview ? "Rebuild" : "Build app";
  const lastAssistant = [...project.chat].reverse().find((m) => m.role === "assistant");
  const stage = stageLabel(lastAssistant?.source ?? project.prime?.source);
  const showStyleBar = Boolean(designBrief && onSaveDesignBrief);
  const hasPlanTurn = project.chat.some((m) => turnKind(m) === "plan");
  const showStandalonePlan =
    isPlan && !busy && !hasPlanTurn && Boolean(project.plan_preview?.row_count || project.row_count || hasPreview);
  const liveTraces = traces.some((e) => e.status === "running");

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
          {stage && <span className={`source-chip ${stage.cls}`}>{stage.text}</span>}
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
              {ctaLabel}
              <ArrowRight size={14} />
            </button>
          )}
        </div>
      </header>

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
          {project.chat.map((m, i) => (
            <MessageTurn key={i} message={m} snapshot={snapshot} onOpenPreview={onOpenPreview} />
          ))}

          {showStandalonePlan && (
            <article className="cursor-turn cursor-turn-plan">
              <div className="cursor-turn-role">Simulacra</div>
              <PlanSection snapshot={snapshot} onOpenPreview={onOpenPreview} />
            </article>
          )}

          {busy && liveTraces && <TracePanel events={traces} compact onCancel={onCancel} />}
          {busy && !liveTraces && <ThinkingLoader label={thinkingLabel} onStop={onCancel} />}

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
            placeholder={isPlan ? "Refine the plan…" : "Ask for changes…"}
            submitLabel="Send"
            modeTag={isPlan ? "Plan" : "Build"}
          />
        </div>
      </div>
    </div>
  );
}
