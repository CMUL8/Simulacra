import {
  ArrowRight,
  Globe,
  PanelLeft,
  PanelLeftClose,
  RotateCcw,
  Square,
} from "lucide-react";
import { Fragment, type ReactNode, useEffect, useRef } from "react";
import type { AgentEvent, ChatMessage, DataRoomFile, DesignBrief, Snapshot } from "../api";
import { DesignBriefForm } from "./DesignBriefForm";
import { PromptComposer } from "./PromptComposer";
import { WaitStage } from "./WaitStage";

type Props = {
  variant: "plan" | "workspace";
  snapshot: Snapshot;
  files: DataRoomFile[];
  input: string;
  busy: boolean;
  error: string | null;
  traces: AgentEvent[];
  sidebarOpen: boolean;
  waitStartedAt?: number | null;
  designBrief?: DesignBrief;
  onSaveDesignBrief?: (v: DesignBrief) => Promise<void>;
  onToggleSidebar: () => void;
  onInput: (v: string) => void;
  onSend: () => void;
  onApprove?: () => void;
  onRebuild?: () => void;
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

function absolutizeUrl(url: string): string {
  if (typeof window === "undefined") return url;
  if (url.startsWith("http://") || url.startsWith("https://")) return url;
  if (url.startsWith("/")) return `${window.location.origin}${url}`;
  return url;
}

function inlineFormat(text: string) {
  let html = escapeHtml(text);
  const codes: string[] = [];
  html = html.replace(/`([^`]+)`/g, (_m, code: string) => {
    codes.push(`<code>${code}</code>`);
    return `\u0000C${codes.length - 1}\u0000`;
  });
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  html = html.replace(
    /\[([^\]]+)\]\((https?:\/\/[^)\s]+|\/projects\/[^)\s]+)\)/g,
    (_m, label: string, href: string) =>
      `<a class="md-link" href="${escapeHtml(absolutizeUrl(href))}" target="_blank" rel="noopener noreferrer">${label}</a>`,
  );
  html = html.replace(/(^|[\s(])(https?:\/\/[^\s<]+)/g, (_m, pre: string, url: string) => {
    const clean = url.replace(/[.,;:!?)]+$/, "");
    const trail = url.slice(clean.length);
    return `${pre}<a class="md-link" href="${escapeHtml(clean)}" target="_blank" rel="noopener noreferrer">${escapeHtml(clean)}</a>${trail}`;
  });
  html = html.replace(/\u0000C(\d+)\u0000/g, (_m, i: string) => codes[Number(i)] || "");
  return html;
}

/** Make Ship share paths absolute when chat still has a relative preview URL. */
function absolutizeShareUrls(text: string): string {
  if (typeof window === "undefined") return text;
  const origin = window.location.origin;
  return text
    .replace(
      /(\*\*Share URL:\*\*[^\n`]*`?)(\/projects\/[^\s`]+)/g,
      (_m, prefix: string, path: string) => `${prefix}${origin}${path}`,
    )
    .replace(/\]\(\/projects\//g, `](${origin}/projects/`);
}

function extractShareUrl(text: string): string | null {
  const md = text.match(/\]\((https?:\/\/[^)\s]+|\/projects\/[^)\s]+)\)/);
  if (md?.[1]) return absolutizeUrl(md[1]);
  const bare = text.match(/https?:\/\/[^\s`]+\/projects\/[^\s`]+/);
  if (bare?.[0]) return bare[0];
  const rel = text.match(/`(\/projects\/[^`]+)`/);
  if (rel?.[1]) return absolutizeUrl(rel[1]);
  return null;
}

function isShipMessage(m: ChatMessage): boolean {
  return m.source === "ship" || /^##\s*Shipped\b/i.test(m.content.trim());
}

/** Document-style markdown — paragraphs, headings, lists (Cursor transcript feel). */
function MarkdownBody({ text }: { text: string }) {
  const blocks = absolutizeShareUrls(text).replace(/\r\n/g, "\n").trim().split(/\n{2,}/);
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

function ShipReceipt({
  text,
  onOpenPreview,
}: {
  text: string;
  onOpenPreview: () => void;
}) {
  const share = extractShareUrl(text);
  return (
    <div className="ship-receipt">
      <h3 className="ship-receipt-title">Shipped</h3>
      <p className="ship-receipt-lead">Approved for your team. Share this preview:</p>
      {share && (
        <a className="ship-receipt-link" href={share} target="_blank" rel="noopener noreferrer">
          {share}
        </a>
      )}
      <div className="ship-receipt-actions">
        <button type="button" className="ship-receipt-btn" onClick={onOpenPreview}>
          Open preview
        </button>
        {share && (
          <button
            type="button"
            className="ship-receipt-btn ghost"
            onClick={() => void navigator.clipboard?.writeText(share)}
          >
            Copy link
          </button>
        )}
      </div>
      <p className="ship-receipt-foot">Keep chatting to iterate — changes stay on this same link.</p>
    </div>
  );
}

function turnKind(m: ChatMessage): TurnKind {
  if (m.role === "user") return "user";
  if (isShipMessage(m)) return "assistant";
  // Structured receipts (## Built / ## Updated) stay as assistant prose, not dim status lines
  if (m.source === "system" && !/^##\s/m.test(m.content.trim())) return "status";
  if (m.role === "assistant" && /^##\s*Plan:/i.test(m.content.trim())) return "plan";
  return "assistant";
}

/** One status chip — never Draft + Deployed at once. */
function statusChip(
  project: Snapshot["project"],
  source?: string | null,
): { text: string; cls: string } | null {
  if (project.deployed) return { text: "Shipped", cls: "source-prime" };
  // Agent edit OR craft personalizer (layout actually changed)
  if (source === "prime" || source === "craft") return { text: "Built", cls: "source-prime" };
  if (source === "cancelled") return { text: "Stopped", cls: "source-error" };
  if (source === "timeout" || source === "error") return { text: "Retry", cls: "source-error" };
  // Plan / template / heuristic preview — not shipped
  if (project.phase === "plan" || source === "template" || source === "heuristic" || !source || source === "none") {
    return { text: "Draft", cls: "source-heuristic" };
  }
  return null;
}

function formatBuildLabel(kind?: string | null): string {
  if (kind === "report") return "Build report";
  if (kind === "slides") return "Build slides";
  if (kind === "one_pager") return "Build one-pager";
  return "Build app";
}

function formatNoun(kind?: string | null): string {
  if (kind === "report") return "report";
  if (kind === "slides") return "slides";
  if (kind === "one_pager") return "one-pager";
  return "app";
}

function formatChipLabel(kind?: string | null): string {
  if (kind === "report") return "Report";
  if (kind === "slides") return "Slides";
  if (kind === "one_pager") return "One-pager";
  return "App";
}

function formatChipHint(kind?: string | null): string {
  if (kind === "report") return "Long-form document — print-friendly, sectioned narrative";
  if (kind === "slides") return "Multi-page deck — one idea per slide";
  if (kind === "one_pager") return "Single printable sheet — dense and scannable";
  return "Interactive command center — filters, tabs, drill-down";
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
          {hasPreview ? "Open preview" : "Preview coming up…"}
        </button>
        <span className="plan-section-hint">
          {hasPreview
            ? "Live preview is ready in the side panel"
            : "Hang tight — preview appears here when this step finishes"}
        </span>
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

  return (
    <article className={`cursor-turn cursor-turn-${kind}`} data-role={kind}>
      {kind === "status" ? (
        <div className="cursor-status-line">{message.content.replace(/\*\*/g, "")}</div>
      ) : kind === "plan" ? (
        <Fragment>
          <MarkdownBody text={message.content} />
          <PlanSection snapshot={snapshot} onOpenPreview={onOpenPreview} compact />
        </Fragment>
      ) : isShipMessage(message) ? (
        <ShipReceipt text={message.content} onOpenPreview={onOpenPreview} />
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
  waitStartedAt = null,
  designBrief,
  onSaveDesignBrief,
  onToggleSidebar,
  onInput,
  onSend,
  onApprove,
  onRebuild,
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
  const noun = formatNoun(project.artifact_kind);
  const buildLabel = formatBuildLabel(project.artifact_kind);
  const thinkingLabel =
    jobKind === "bootstrap" || (isPlan && waitingForOpen)
      ? `Building ${noun}…`
      : jobKind === "build_run"
        ? `Builder customizing ${noun}…`
        : jobKind === "iterate_run"
          ? `Builder updating ${noun}…`
          : "Working…";
  const lastAssistant = [...project.chat].reverse().find((m) => m.role === "assistant");
  const stage = statusChip(project, lastAssistant?.source ?? project.prime?.source);
  const showStyleBar = Boolean(designBrief && onSaveDesignBrief);
  const hasPlanTurn = project.chat.some((m) => turnKind(m) === "plan");
  const showStandalonePlan =
    isPlan && !busy && !hasPlanTurn && Boolean(project.plan_preview?.row_count || project.row_count || hasPreview);

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
        </div>
        <div className="agent-topbar-right">
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
              {buildLabel}
              <ArrowRight size={14} />
            </button>
          )}
          {!isPlan && onRebuild && (
            <button
              type="button"
              className="ghost-btn quiet"
              disabled={busy}
              onClick={onRebuild}
              title="Reset to scaffold and run the builder again"
            >
              Rebuild from draft
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
              <PlanSection snapshot={snapshot} onOpenPreview={onOpenPreview} />
            </article>
          )}

          {busy ? (
            <div className="cursor-turn cursor-turn-wait">
              <WaitStage
                variant="thread"
                title={thinkingLabel.replace(/…$/, "")}
                subtitle={
                  jobKind === "iterate_run"
                    ? "Applying your change — preview refreshes when done"
                    : `Building your ${noun} — preview opens when this finishes`
                }
                jobKind={jobKind}
                traces={traces}
                startedAt={waitStartedAt}
                onStop={onCancel}
              />
            </div>
          ) : null}

          <div ref={endRef} />
        </div>

        <div className="agent-composer-wrap">
          <div className="composer-chrome" role="toolbar" aria-label="Project actions">
            <div className="composer-chrome-left">
              {stage && <span className={`composer-chrome-chip source-chip ${stage.cls}`}>{stage.text}</span>}
              {showStyleBar && (
                <DesignBriefForm
                  compact
                  value={designBrief!}
                  onSave={onSaveDesignBrief!}
                  disabled={busy}
                />
              )}
              <span
                className="composer-chrome-btn format"
                title={formatChipHint(project.artifact_kind)}
              >
                {formatChipLabel(project.artifact_kind)}
              </span>
              <button
                type="button"
                className="composer-chrome-btn"
                disabled={!hasPreview}
                onClick={onOpenPreview}
                title={hasPreview ? "Open preview" : "Preview appears when the build finishes"}
              >
                <Globe size={13} strokeWidth={1.75} />
                {hasPreview ? "Preview" : "Preview…"}
              </button>
            </div>
            <div className="composer-chrome-right">
              {busy && onCancel && (
                <button
                  type="button"
                  className="composer-chrome-btn stop"
                  onClick={onCancel}
                  title="Stop current job"
                >
                  <Square size={11} fill="currentColor" />
                  Stop
                </button>
              )}
            </div>
          </div>
          <PromptComposer
            value={input}
            onChange={onInput}
            onSubmit={onSend}
            onCancel={onCancel}
            disabled={project.status === "failed"}
            busy={busy}
            files={files}
            placeholder={
              isPlan
                ? "Ask about the plan or style…"
                : "Tell the builder what to change…"
            }
            submitLabel="Send"
            modeTag={isPlan ? "Plan" : "Agent"}
          />
        </div>
      </div>
    </div>
  );
}
