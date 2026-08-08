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
    if (html.startsWith("## ")) {
      html = `<span class="md-h2">${html.slice(3)}</span>`;
    } else if (html.startsWith("- ")) {
      html = `<span class="md-li">${html.slice(2)}</span>`;
    }
    return <p key={i} dangerouslySetInnerHTML={{ __html: html || "&nbsp;" }} />;
  });
}

/** User-facing stage chips — never expose engine names. */
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

function PlanCard({
  snapshot,
  onOpenPreview,
}: {
  snapshot: Snapshot;
  onOpenPreview: () => void;
}) {
  const p = snapshot.project;
  const preview = p.plan_preview;
  const hasPreview = Boolean(snapshot.preview_url);
  const rows = preview?.row_count ?? p.row_count ?? 0;
  const high = preview?.high_risk ?? 0;
  const vendors = preview?.vendors?.length ?? 0;
  const files = (preview?.files ?? []).slice(0, 4);

  return (
    <div className="plan-card">
      <div className="plan-card-head">
        <span className="plan-card-kicker">Plan</span>
        <h2>{p.app_config?.title || "Your app"}</h2>
        {p.app_config?.subtitle && <p className="plan-card-sub">{p.app_config.subtitle}</p>}
      </div>
      <dl className="plan-card-facts">
        <div>
          <dt>Data</dt>
          <dd>
            {rows} rows
            {high ? ` · ${high} high risk` : ""}
            {vendors ? ` · ${vendors} vendors` : ""}
          </dd>
        </div>
        {files.length > 0 && (
          <div>
            <dt>Sources</dt>
            <dd>{files.map((f) => f.name).join(", ")}</dd>
          </div>
        )}
      </dl>
      <div className="plan-card-actions">
        <button
          type="button"
          className={`approve-btn ${hasPreview ? "" : "quiet"}`}
          disabled={!hasPreview}
          onClick={onOpenPreview}
        >
          <Globe size={14} />
          {hasPreview ? "Open draft preview" : "Draft preview preparing…"}
        </button>
        <p className="plan-card-hint">
          {hasPreview
            ? "Review the draft, then Build app when the plan looks right."
            : "Hang on — preparing a draft you can review."}
        </p>
      </div>
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
      ? "Preparing plan & draft…"
      : jobKind === "build_run"
        ? "Building app…"
        : "Working…";
  const ctaLabel = isPlan ? "Build app" : hasPreview ? "Rebuild" : "Build app";
  const lastAssistant = [...project.chat].reverse().find((m) => m.role === "assistant");
  const stage = stageLabel(lastAssistant?.source ?? project.prime?.source);
  const showStyleBar = Boolean(designBrief && onSaveDesignBrief);
  const showPlanCard = isPlan && Boolean(project.plan_preview?.row_count || project.row_count || hasPreview);
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

      {isPlan && (
        <p className="policy-whisper">Review the plan, open the draft, then Build app.</p>
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

          {showPlanCard && !busy && <PlanCard snapshot={snapshot} onOpenPreview={onOpenPreview} />}

          {busy && liveTraces && (
            <TracePanel events={traces} compact onCancel={onCancel} />
          )}

          {busy && !liveTraces && (
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
            placeholder={isPlan ? "Refine the plan…" : "Ask for changes…"}
            submitLabel="Send"
            modeTag={isPlan ? "Plan" : "Build"}
          />
        </div>
      </div>
    </div>
  );
}
