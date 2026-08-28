import {
  ArrowRight,
  Copy,
  FileText,
  Globe,
  ListChecks,
  MessageSquare,
  MoreHorizontal,
  PanelLeft,
  PanelLeftClose,
  RotateCcw,
  Square,
  ThumbsDown,
  ThumbsUp,
  Users,
  Flag,
} from "lucide-react";
import { Fragment, type ReactNode, useEffect, useRef, useState } from "react";
import { createMissionAgent, getCmul8Room, getMission, subscribeEvents, type AgentEvent, type ChatMessage, type Checkpoint, type Cmul8RoomPayload, type DataRoomFile, type MissionAgentRecommendation, type MissionOverview, type Snapshot } from "../api";
import { userFacingFiles } from "../lib/userFacingFiles";
import { AnswerBlock } from "./agent/AnswerBlock";
import { ApprovalCard } from "./agent/ApprovalCard";
import {
  SelectionActions,
  selectionPrompt,
} from "./agent/SelectionActions";
import {
  ThinkingTrail,
  tracesForWait,
  type ThoughtSnapshot,
} from "./agent/ThinkingTrail";
import { PromptComposer } from "./PromptComposer";
import { VersionsMenu } from "./VersionsMenu";
import { WaitStage } from "./WaitStage";
import { ProjectRoomContainer } from "../features/project-room";
import { MissionPod } from "../features/missions";
import { OnboardingChecklist } from "../features/workplace/onboarding/OnboardingChecklist";

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
  onToggleSidebar: () => void;
  onInput: (v: string) => void;
  onSend: () => void;
  onApprove?: () => void;
  onRebuild?: () => void;
  onCancel?: () => void;
  onOpenPreview: () => void;
  onGovernance: () => void;
  onRollback?: (checkpointId?: string) => void;
  onDismissError: () => void;
  onNew?: () => void;
  onRetry?: (text: string) => void;
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

function scrubCodeFilenames(text: string): string {
  // Drop legacy "What changed" inventories (Title/Layout/Styles/App.tsx bullets)
  let out = text.replace(
    /(?:^|\n)\s*\*{0,2}What changed\*{0,2}\s*\n(?:[ \t]*[-*].*\n?)*/gi,
    "\n",
  );
  out = out.replace(
    /^\s*[-*]\s*(?:Title\s*&\s*(?:config|framing)|Layout\s*(?:\/\s*UI|&\s*structure).*|Styles?(?:\s*\(.*\))?|Visual styling|Content update|Data view|Summary metrics|Research content)\s*$/gim,
    "",
  );
  out = out
    .replace(
      /\s*\((?:`?(?:src\/)?(?:App\.tsx|styles\.css|main\.tsx)`?|`?[\w./-]+\.(?:tsx?|jsx?|css)`?)\)/gi,
      "",
    )
    .replace(/\b(?:src\/)?(?:App\.tsx|styles\.css|main\.tsx)\b/gi, "")
    .replace(/`[^`\n]*\.(?:tsx?|jsx?|css|py)`/gi, "")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ");
  return out;
}

function rewritePhantomControls(text: string, mode: "plan" | "workspace" = "workspace"): string {
  const buildCta = mode === "plan" ? "Confirm below" : "Open Preview";
  let out = text
    .replace(/(?:one\s+click\s+on|click\s+on|(?:hit|press|click|tap)\s+)\s*\*{0,2}build\*{0,2}/gi, buildCta)
    .replace(/\*{0,2}Rebuild from draft\*{0,2}/gi, "**Start over**")
    .replace(/\bretry\s+\*{0,2}Build(?:\s+(?:app|report|slides|one-pager))?\*{0,2}/gi, "use **Start over**")
    .replace(/_\(Build first — then I can apply edits\.\)_/gi, "Confirm below first — then I can apply edits.")
    .replace(/You can refine or Approve again\.?/gi, "You can refine in chat, or Start over.")
    .replace(/\bBuild complete\s*[—–-]\s*open\s+\*{0,2}Preview\*{0,2}\s+to review\.?/gi, "It's in Preview.")
    .replace(/when you['’]re ready,\s*Confirm below/gi, "Confirm below when you’re ready")
    .replace(/\bHitting iterate\b/gi, "Updating")
    .replace(/\s*\(since Serper web search isn't configured\)/gi, "")
    .replace(/\s*\(craft fallback[^)]*\)/gi, "")
    .replace(/\bagent file edits incomplete\.?/gi, "")
    .replace(/Layout was personalized from your Style brief[^.]*\./gi, "Preview is ready.")
    .replace(/^[^\n]*Sources?:\s*\d+\s+rows[^\n]*$/gim, "")
    .replace(/^(?:`?[\w.-]+\.(?:json|csv|md)`?(?:\s*[,·•]\s*)?){2,}\s*$/gim, "")
    .replace(/^\s*All saved to `?[\w./-]+\.(?:json|md|csv)`?\.?\s*$/gim, "");
  if (mode === "workspace") {
    out = out.replace(/\bConfirm below\b/gi, "Open Preview");
  }
  return out;
}

function asksToBuild(text: string): boolean {
  return /(?:one\s+click\s+on|click\s+on|(?:hit|press|click|tap)\s+)\s*\*{0,2}build\*{0,2}|confirm below|give the go-ahead|ready when you are|whenever you say go/i.test(
    text,
  );
}

function truncateMiddle(s: string, max = 42): string {
  if (s.length <= max) return s;
  const keep = max - 1;
  const head = Math.ceil(keep * 0.58);
  const tail = keep - head;
  return `${s.slice(0, head)}…${s.slice(-tail)}`;
}

function isPreviewHref(href: string): boolean {
  return /\/projects\/[^/\s]+/i.test(href);
}

function linkHtml(label: string, href: string): string {
  const abs = absolutizeUrl(href);
  const safe = escapeHtml(abs);
  if (isPreviewHref(href)) {
    return `<a class="md-link-chip" data-preview="1" href="${safe}" target="_blank" rel="noopener noreferrer">Open preview</a>`;
  }
  const shown = label === href || label === abs ? truncateMiddle(href) : truncateMiddle(label, 56);
  return `<a class="md-link" href="${safe}" target="_blank" rel="noopener noreferrer">${escapeHtml(shown)}</a>`;
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
    (_m, label: string, href: string) => linkHtml(label, href),
  );
  html = html.replace(/(^|[\s(])(https?:\/\/[^\s<]+)/g, (_m, pre: string, url: string) => {
    const clean = url.replace(/[.,;:!?)]+$/, "");
    const trail = url.slice(clean.length);
    return `${pre}${linkHtml(clean, clean)}${trail}`;
  });
  html = html.replace(/\u0000C(\d+)\u0000/g, (_m, i: string) => codes[Number(i)] || "");
  return html;
}

const LIST_ITEM_RE = /^(\s*)(?:[-*+•]|[–—]|\d+[.)])\s+(\S.*)$/;
const WIDGET_INVENTORY =
  /^(kpi|chart|tables?|findings|leaderboard|empty state|dashboard|timeline|scorecard|strip|map|filters?|vendor)\b/i;

function isWidgetInventory(items: string[]): boolean {
  if (items.length < 3) return false;
  const hits = items.filter((raw) => {
    const label = raw.replace(/^\*\*/, "").split(/[:—–*]/)[0]!.trim();
    return WIDGET_INVENTORY.test(label);
  }).length;
  return hits >= 3 && hits >= Math.ceil(items.length * 0.6);
}

function listMatch(line: string): { indent: number; kind: "ul" | "ol"; text: string } | null {
  const trimmed = line.trimEnd();
  if (/^[-*_•]{3,}$/.test(trimmed.trim())) return null;
  const m = trimmed.match(LIST_ITEM_RE);
  if (!m) return null;
  const indent = (m[1] || "").replace(/\t/g, "    ").length;
  const ordered = /^\s*\d+[.)]\s+/.test(trimmed);
  return { indent, kind: ordered ? "ol" : "ul", text: m[2] || "" };
}

/** Bold the lead-in (`KPI strip: …` / `**Title** — …`) so lists read like Cursor, not markdown. */
function formatListItem(raw: string): string {
  const t = raw.trim();
  const labeled = t.match(/^(?:\*\*)?([^*:—–]{2,42})(?:\*\*)?\s*[:—–]\s+(.+)$/);
  if (labeled && !/https?:|\/\//.test(labeled[1]!)) {
    const label = labeled[1]!.trim();
    const rest = labeled[2]!;
    return `<strong class="md-li-label">${escapeHtml(label)}</strong> <span class="md-li-rest">${inlineFormat(rest)}</span>`;
  }
  return inlineFormat(t);
}

/** Drop blank lines that only exist between list items so loose markdown stays one list. */
function tightenLooseLists(src: string): string {
  const lines = src.split("\n");
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!;
    if (!line.trim()) {
      const prev = out.length ? out[out.length - 1]! : "";
      let j = i + 1;
      while (j < lines.length && !lines[j]!.trim()) j++;
      const next = j < lines.length ? lines[j]! : "";
      if (listMatch(prev) && listMatch(next)) continue;
    }
    out.push(line);
  }
  return out.join("\n");
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

/** Document-style markdown — no raw pipes, hash headings, or leftover dashes. */
function MarkdownBody({
  text,
  onOpenPreview,
  ctaMode = "workspace",
}: {
  text: string;
  onOpenPreview?: () => void;
  ctaMode?: "plan" | "workspace";
}) {
  const cleaned = tightenLooseLists(
    rewritePhantomControls(
      scrubCodeFilenames(absolutizeShareUrls(text).replace(/\r\n/g, "\n")),
      ctaMode,
    ),
  ).trim();
  const blocks = cleaned.split(/\n{2,}/);
  const nodes: ReactNode[] = [];

  const parseCells = (line: string) =>
    line
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim().replace(/^`|`$/g, ""));

  const humanFile = (raw: string) => {
    const base = raw.split("/").pop() || raw;
    const stem = base.replace(/\.[a-z0-9]+$/i, "").replace(/^\d+[_\-\s]*/, "").replace(/[_-]+/g, " ").trim();
    return stem ? stem.charAt(0).toUpperCase() + stem.slice(1) : "Source";
  };

  const emitList = (kind: "ul" | "ol", items: string[], key: string) => {
    if (isWidgetInventory(items)) {
      return (
        <p key={key} className="md-soft">
          The preview holds the layout — charts, tables, and empty states live there.
        </p>
      );
    }
    const Tag = kind === "ol" ? "ol" : "ul";
    return (
      <Tag key={key} className={kind === "ol" ? "md-ol" : "md-ul"}>
        {items.map((item, li) => (
          <li key={li} dangerouslySetInnerHTML={{ __html: formatListItem(item) }} />
        ))}
      </Tag>
    );
  };

  const emitFlow = (rawLines: string[], key: string) => {
    const lines = rawLines.filter((l) => l.trim().length > 0);
    const out: ReactNode[] = [];
    let i = 0;
    let n = 0;
    while (i < lines.length) {
      const hit = listMatch(lines[i]!);
      if (hit) {
        const items: string[] = [];
        const kind = hit.kind;
        while (i < lines.length) {
          const m = listMatch(lines[i]!);
          if (!m || m.kind !== kind) break;
          let text = m.text;
          i += 1;
          while (i < lines.length) {
            const nxt = lines[i]!;
            if (listMatch(nxt)) break;
            const lead = (nxt.match(/^\s*/)?.[0] || "").replace(/\t/g, "    ").length;
            if (lead <= m.indent) break;
            text += ` ${nxt.trim()}`;
            i += 1;
          }
          items.push(text);
        }
        out.push(emitList(kind, items, `${key}-l-${n++}`));
        continue;
      }

      let t = lines[i]!.trim();
      i += 1;
      if (!t) continue;
      const hm = t.match(/^#{1,3}\s+(.*)$/);
      if (hm) {
        const title = (hm[1] || "").trim();
        if (
          /^(what.?s in (the )?data room|in the preview|sources?|files?|inventory|what the (app|report) can show)\b/i.test(
            title,
          )
        ) {
          continue;
        }
        out.push(
          <p key={`${key}-h-${n++}`} className="md-section">
            <span dangerouslySetInnerHTML={{ __html: inlineFormat(title) }} />
          </p>,
        );
        continue;
      }
      if (
        lines.length === 1 &&
        t.length < 48 &&
        !/[.!?]$/.test(t) &&
        !listMatch(t)
      ) {
        const label = t.replace(/^#+\s*/, "").trim();
        if (
          /^(what.?s in (the )?data room|in the preview|sources?|files?|inventory)\b/i.test(label) ||
          /^sources are in the data room\.?$/i.test(label)
        ) {
          continue;
        }
        out.push(
          <p key={`${key}-sec-${n++}`} className="md-section">
            {label}
          </p>,
        );
        continue;
      }
      if (/^sources are in the data room\.?$/i.test(t)) continue;
      if (/^added\b.+\bto (your |the )?(sources|data room)\b/i.test(t)) continue;
      if (/^\|/.test(t) && t.includes("|")) {
        const cells = parseCells(t);
        if (/\.(json|md|csv)\b/i.test(cells[0] || "")) continue;
        out.push(
          <div key={`${key}-or-${n++}`} className="md-card">
            <span className="md-card-title">{humanFile(cells[0] || "")}</span>
            {cells[1] ? <span className="md-card-body">{cells.slice(1).join(" — ")}</span> : null}
          </div>,
        );
        continue;
      }
      if (t.startsWith("**") && t.endsWith("**") && t.indexOf("**", 2) === t.length - 2) {
        out.push(
          <p key={`${key}-p-${n++}`} className="md-lead" dangerouslySetInnerHTML={{ __html: inlineFormat(t) }} />,
        );
      } else {
        out.push(
          <p key={`${key}-p-${n++}`} dangerouslySetInnerHTML={{ __html: inlineFormat(t) }} />,
        );
      }
    }
    return out;
  };

  blocks.forEach((block, bi) => {
    const lines = block.split("\n").filter((l) => l.trim().length > 0);
    if (!lines.length) return;

    const pipeLines = lines.filter((l) => l.includes("|") && /^\s*\|/.test(l));
    const looksLikeTable =
      pipeLines.length >= 1 &&
      lines.every((l) => !l.trim() || l.includes("|") || /^[-:| ]+$/.test(l.trim()));
    if (looksLikeTable) {
      const rows = pipeLines
        .filter((l) => !/^[-:| ]+$/.test(l.replace(/\|/g, "").trim()))
        .map(parseCells)
        .filter((cells) => {
          const j = cells.join(" ").toLowerCase();
          return j !== "file contents" && cells[0]?.toLowerCase() !== "file";
        });
      const fileish = rows.filter((c) => /\.(json|md|csv|tsv|txt)\b/i.test(c[0] || "")).length;
      if (!rows.length || fileish >= Math.max(1, Math.ceil(rows.length / 2))) {
        return;
      }
      nodes.push(
        <ul key={`cards-${bi}`} className="md-cards">
          {rows.map((cells, ri) => {
            const title = humanFile(cells[0] || "");
            const body = cells.slice(1).filter(Boolean).join(" — ");
            return (
              <li key={ri} className="md-card">
                <span className="md-card-title">{title}</span>
                {body ? <span className="md-card-body">{body}</span> : null}
              </li>
            );
          })}
        </ul>,
      );
      return;
    }

    nodes.push(...emitFlow(block.split("\n"), `b${bi}`));
  });

  return (
    <div
      className="cursor-prose"
      onClick={(e) => {
        const hit = (e.target as HTMLElement).closest("a.md-link-chip[data-preview]");
        if (!hit || !onOpenPreview) return;
        e.preventDefault();
        onOpenPreview();
      }}
    >
      {nodes}
    </div>
  );
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
      {share ? (
        <div className="md-link-chip-row">
          <button type="button" className="md-link-chip" onClick={onOpenPreview}>
            Open preview
          </button>
          <button
            type="button"
            className="md-link-chip ghost"
            onClick={() => void navigator.clipboard?.writeText(share)}
          >
            Copy link
          </button>
        </div>
      ) : null}
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

function isOrphanJobStatus(m: ChatMessage): boolean {
  const text = m.content.trim();
  // Old builds left "Building your app…" in chat forever — never show it.
  if (m.source === "system" && /^Building your\b/i.test(text)) return true;
  // Inventory spam — filenames / "Added … to your sources"
  if (/^Added\b.+\bto (your |the )?(sources|data room)\b/i.test(text)) return true;
  if (/^Sources are in the data room\.?$/i.test(text)) return true;
  if (/\b(design_brief|kernel-state|kernel_state|agent_context)\.(json|md)\b/i.test(text)) return true;
  // Legacy rollback jargon — hide; restores use plain copy
  if (/^Rolled back to checkpoint/i.test(text)) return true;
  if (/^Undid — preview restored/i.test(text)) return true;
  return false;
}

function turnKind(m: ChatMessage): TurnKind {
  if (m.role === "user") return "user";
  if (isShipMessage(m)) return "assistant";
  // Structured receipts (## Built / ## Updated) stay as assistant prose, not dim status lines
  if (m.source === "system" && !/^##\s/m.test(m.content.trim())) return "status";
  if (m.role === "assistant" && /^##\s*Plan:/i.test(m.content.trim())) return "plan";
  return "assistant";
}

function lastUserTextBefore(chat: ChatMessage[], idx: number): string | null {
  for (let i = idx - 1; i >= 0; i--) {
    if (turnKind(chat[i]!) === "user") return chat[i]!.content;
  }
  return null;
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
  // Plan — user is steering the agent before Build
  if (project.phase === "plan") {
    return { text: "Plan", cls: "source-heuristic" };
  }
  // Template / heuristic preview — not shipped
  if (source === "template" || source === "heuristic" || !source || source === "none") {
    return { text: "Draft", cls: "source-heuristic" };
  }
  return null;
}

function agentNeedsLine(
  project: Snapshot["project"],
  busy: boolean,
  jobKind?: string | null,
): { label: string; detail: string; title: string } {
  const room = project.plan_preview?.source_room;
  const files = project.plan_preview?.files ?? [];
  const names = room?.file_names?.length
    ? room.file_names
    : files.map((f) => f.name).filter(Boolean);
  const empty = room?.empty ?? names.length === 0;
  const sources = empty
    ? "No sources"
    : `${names.length} source${names.length === 1 ? "" : "s"}`;
  const sourcesTitle = empty
    ? "No sources attached yet"
    : names.join(", ");
  if (busy && (jobKind === "agent_chat" || jobKind === "plan_ask" || waitingChatJob(jobKind))) {
    return { label: "Working", detail: sources, title: sourcesTitle };
  }
  if (busy && (jobKind === "build_run" || jobKind === "bootstrap")) {
    return { label: "Building", detail: sources, title: sourcesTitle };
  }
  if (busy && jobKind === "iterate_run") {
    return { label: "Updating", detail: sources, title: sourcesTitle };
  }
  return { label: "Agent", detail: sources, title: sourcesTitle };
}

function waitingChatJob(jobKind?: string | null): boolean {
  return !jobKind || jobKind === "agent_chat" || jobKind === "plan_ask";
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

  return (
    <div className={`plan-section ${compact ? "compact" : ""}`}>
      {!compact && (
        <div className="plan-section-head">
          <h2>{p.app_config?.title || "Your app"}</h2>
          {p.app_config?.subtitle && <p className="plan-section-sub">{p.app_config.subtitle}</p>}
        </div>
      )}
      {rows > 0 ? (
        <div className="plan-section-meta">
          <span>{rows} source row{rows === 1 ? "" : "s"}</span>
        </div>
      ) : null}
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

function TurnActions({
  text,
  retryText,
  busy,
  onRetry,
}: {
  text: string;
  retryText?: string | null;
  busy?: boolean;
  onRetry?: (text: string) => void;
}) {
  const [copied, setCopied] = useState(false);
  const [vote, setVote] = useState<"up" | "down" | null>(null);
  const copyTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
    };
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (copyTimer.current) window.clearTimeout(copyTimer.current);
      copyTimer.current = window.setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard may be blocked */
    }
  }

  return (
    <div className={`turn-actions${vote || copied ? " is-on" : ""}`} role="toolbar" aria-label="Response actions">
      <button type="button" className="turn-action" onClick={copy} title={copied ? "Copied" : "Copy"} aria-label="Copy">
        <Copy size={14} strokeWidth={1.5} />
      </button>
      {retryText && onRetry ? (
        <button
          type="button"
          className="turn-action"
          disabled={busy}
          onClick={() => onRetry(retryText)}
          title="Retry"
          aria-label="Retry"
        >
          <RotateCcw size={14} strokeWidth={1.5} />
        </button>
      ) : null}
      <button
        type="button"
        className={`turn-action${vote === "up" ? " on" : ""}`}
        onClick={() => setVote((v) => (v === "up" ? null : "up"))}
        title="Good response"
        aria-pressed={vote === "up"}
      >
        <ThumbsUp size={14} strokeWidth={1.5} />
      </button>
      <button
        type="button"
        className={`turn-action${vote === "down" ? " on" : ""}`}
        onClick={() => setVote((v) => (v === "down" ? null : "down"))}
        title="Bad response"
        aria-pressed={vote === "down"}
      >
        <ThumbsDown size={14} strokeWidth={1.5} />
      </button>
    </div>
  );
}

function ChromeMore({
  sidebarOpen,
  busy,
  onAccount,
  onRebuild,
}: {
  sidebarOpen: boolean;
  busy: boolean;
  onAccount: () => void;
  onRebuild?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const showAccount = !sidebarOpen;
  const showRebuild = Boolean(onRebuild);
  if (!showAccount && !showRebuild) return null;

  return (
    <div className="chrome-more" ref={rootRef}>
      <button
        type="button"
        className="composer-action icon-only"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="More actions"
        onClick={() => setOpen((v) => !v)}
      >
        <MoreHorizontal size={16} strokeWidth={1.5} />
      </button>
      {open ? (
        <ul className="chrome-more-menu" role="menu">
          {showAccount ? (
            <li>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  onAccount();
                }}
              >
                Account
              </button>
            </li>
          ) : null}
          {showRebuild ? (
            <li>
              <button
                type="button"
                role="menuitem"
                className="danger"
                disabled={busy}
                onClick={() => {
                  setOpen(false);
                  onRebuild?.();
                }}
              >
                Start over
              </button>
            </li>
          ) : null}
        </ul>
      ) : null}
    </div>
  );
}

function MessageTurn({
  message,
  snapshot,
  onOpenPreview,
  actions,
}: {
  message: ChatMessage;
  snapshot: Snapshot;
  isLatestAssistant?: boolean;
  busy?: boolean;
  onOpenPreview: () => void;
  actions?: ReactNode;
}) {
  const kind = turnKind(message);

  return (
    <article className={`cursor-turn cursor-turn-${kind}`} data-role={kind}>
      {kind === "status" ? (
        <div className="cursor-status-line">{message.content.replace(/\*\*/g, "")}</div>
      ) : kind === "plan" ? (
        <Fragment>
          <div className="cursor-answer">
            <AnswerBlock>
              <MarkdownBody
                text={message.content}
                onOpenPreview={onOpenPreview}
                ctaMode={snapshot.project.phase === "plan" ? "plan" : "workspace"}
              />
            </AnswerBlock>
          </div>
          <PlanSection snapshot={snapshot} onOpenPreview={onOpenPreview} compact />
        </Fragment>
      ) : isShipMessage(message) ? (
        <ShipReceipt text={message.content} onOpenPreview={onOpenPreview} />
      ) : kind === "user" ? (
        <div className="cursor-user-bubble">
          <MarkdownBody text={message.content} />
        </div>
      ) : (
        <div className="cursor-answer">
          <AnswerBlock>
            <MarkdownBody
              text={message.content}
              onOpenPreview={onOpenPreview}
              ctaMode={snapshot.project.phase === "plan" ? "plan" : "workspace"}
            />
          </AnswerBlock>
          {actions}
        </div>
      )}
    </article>
  );
}

function CrewRecommendations({ recommendations, adding, canCreate, onAdd, onCustomize }: { recommendations: MissionAgentRecommendation[]; adding?: string; canCreate: boolean; onAdd: (recommendation: MissionAgentRecommendation) => void; onCustomize: (recommendation: MissionAgentRecommendation) => void }) {
  if (!recommendations.length) return null;
  return <section className="crew-recommendations" aria-label="Recommended crew"><header><span>RECOMMENDED CREW</span><p>Start with a crew shaped around this Mission.</p></header><div>{recommendations.map((recommendation) => <article key={recommendation.slug || recommendation.name}><span className="crew-recommendation-avatar" aria-hidden>{recommendation.name.slice(0, 1).toUpperCase()}</span><div><strong>{recommendation.name}</strong><small>{recommendation.role}</small><p>{recommendation.rationale}</p></div><footer><button type="button" onClick={() => onAdd(recommendation)} disabled={!canCreate || adding === (recommendation.slug || recommendation.name)}>{adding === (recommendation.slug || recommendation.name) ? "Adding…" : "Add"}</button><button type="button" className="secondary" disabled={!canCreate} onClick={() => onCustomize(recommendation)}>Customize</button></footer></article>)}</div></section>;
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
  onRetry,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const prevBusy = useRef(busy);
  const [lastThought, setLastThought] = useState<ThoughtSnapshot | null>(null);
  const [missionOpen, setMissionOpen] = useState(false);
  const [missionFocus, setMissionFocus] = useState<"summary" | "crew">("summary");
  const [workspaceTab, setWorkspaceTab] = useState<"chat" | "work" | "files">("chat");
  const [missionData, setMissionData] = useState<MissionOverview | null>(null);
  const [roomData, setRoomData] = useState<Cmul8RoomPayload | null>(null);
  const [assignAsTask, setAssignAsTask] = useState(false);
  const [assignmentError, setAssignmentError] = useState("");
  const [selectedCrew, setSelectedCrew] = useState<{ kind: "agent" | "human"; id: string } | null>(null);
	const [addingRecommendation, setAddingRecommendation] = useState("");
	const [customizingRecommendation, setCustomizingRecommendation] = useState<MissionAgentRecommendation | null>(null);
  const project = snapshot.project;
  const isPlan = variant === "plan";
  const hasPreview = Boolean(snapshot.preview_url);
  const waitingForOpen = busy && !project.chat.some((m) => m.role === "assistant");
  const jobKind = undefined;
  const noun = formatNoun(project.artifact_kind);
  const buildLabel = formatBuildLabel(project.artifact_kind);
  const thinkingLabel =
    jobKind === "agent_chat" || jobKind === "plan_ask" || (waitingForOpen && !jobKind)
      ? "Agent…"
      : jobKind === "bootstrap"
        ? `Building ${noun}…`
        : jobKind === "build_run"
          ? `Builder customizing ${noun}…`
          : jobKind === "iterate_run"
            ? `Builder updating ${noun}…`
            : "Working…";
  const lastAssistant = [...project.chat].reverse().find((m) => m.role === "assistant");
  const stage = statusChip(project, lastAssistant?.source);
  const needs = agentNeedsLine(project, busy, jobKind);
  const agentWantsBuild =
    isPlan && Boolean(lastAssistant?.content && asksToBuild(lastAssistant.content));
  const hasPlanTurn = project.chat.some((m) => turnKind(m) === "plan");
  const visibleChat = project.chat.filter((m) => !isOrphanJobStatus(m));
  const missionAgents = missionData?.agents ?? [];
  const humanMembers = (roomData?.room.members ?? []).filter((member) => member.actor_type === "human" || !member.actor_type);
  const canCreateMissionAgents = Boolean(roomData?.permissions.review_graph);
  const canInviteMissionHumans = Boolean(roomData?.permissions.invite);
  const agentHandle = (name: string) => `@${name.trim().replace(/\s+/g, "_")}`;
  const mentionedAgentIds = missionAgents
    .filter((agent) => new RegExp(`(^|\\s)${agentHandle(agent.name).replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=\\s|$|[.,!?])`, "i").test(input))
    .map((agent) => agent.id);
  const crewMentioned = /(^|\s)@Crew(?=\s|$|[.,!?])/i.test(input);
  const mentionedHumans = humanMembers.filter((member) => {
    const handle = agentHandle(member.display_name || member.actor_id);
    return new RegExp(`(^|\\s)${handle.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?=\\s|$|[.,!?])`, "i").test(input);
  });

  useEffect(() => {
    if (mentionedAgentIds.length || crewMentioned) setAssignAsTask(true);
  }, [mentionedAgentIds.join("|"), crewMentioned]);

  const refreshMission = async () => {
    try {
      const [mission, room] = await Promise.all([getMission(project.id), getCmul8Room(project.id).catch(() => null)]);
      setMissionData(mission); setRoomData(room); setAssignmentError("");
    }
    catch (cause) { setAssignmentError(cause instanceof Error ? cause.message : "Could not load Mission crew"); }
  };

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [mission, room] = await Promise.all([getMission(project.id), getCmul8Room(project.id).catch(() => null)]);
        if (!cancelled) { setMissionData(mission); setRoomData(room); }
      } catch { if (!cancelled) { setMissionData(null); setRoomData(null); } }
    };
    void load();
    const wake = () => { if (document.visibilityState !== "hidden") void load(); };
    const unsubscribe = subscribeEvents(project.id, () => { void load(); });
    const timer = window.setInterval(wake, 15000);
    document.addEventListener("visibilitychange", wake);
    window.addEventListener("missions:refresh", wake);
    return () => { cancelled = true; unsubscribe(); window.clearInterval(timer); document.removeEventListener("visibilitychange", wake); window.removeEventListener("missions:refresh", wake); };
  }, [project.id]);

  const mentionAgent = (name: string) => {
    const handle = agentHandle(name);
    const separator = input && !input.endsWith(" ") ? " " : "";
    onInput(`${input}${separator}${handle} `);
    setAssignAsTask(true);
  };
	const addRecommendation = async (recommendation: MissionAgentRecommendation) => {
		if (addingRecommendation) return;
		const key = recommendation.slug || recommendation.name;
		setAddingRecommendation(key); setAssignmentError("");
		try {
			await createMissionAgent(project.id, { name: recommendation.name, role: recommendation.role, mandate: recommendation.mandate, scope: recommendation.scope, autonomy: recommendation.autonomy });
			await refreshMission(); window.dispatchEvent(new Event("missions:refresh"));
		} catch (cause) { setAssignmentError(cause instanceof Error ? cause.message : "Could not add the recommended agent"); }
		finally { setAddingRecommendation(""); }
	};
  const showStandalonePlan =
    isPlan && !busy && !hasPlanTurn && Boolean(project.plan_preview?.row_count || project.row_count || hasPreview);
  const lastAssistantIdx = (() => {
    for (let i = visibleChat.length - 1; i >= 0; i--) {
      if (visibleChat[i] && turnKind(visibleChat[i]!) === "assistant") return i;
    }
    return -1;
  })();
  const chatWait =
    jobKind === "agent_chat" || jobKind === "plan_ask" || (isPlan && waitingForOpen && !jobKind);
  // Snapshot the thinking trail when a chat turn finishes (Cursor: collapse after answer).
  useEffect(() => {
    const wasBusy = prevBusy.current;
    prevBusy.current = busy;
    if (wasBusy && !busy && chatWait && waitStartedAt) {
      setLastThought({
        events: tracesForWait(traces, waitStartedAt),
        startedAt: waitStartedAt,
        endedAt: Date.now(),
      });
    }
  }, [busy, chatWait, waitStartedAt, traces]);

  // New user send clears prior collapsed trail once we're live again.
  useEffect(() => {
    if (busy && chatWait) setLastThought(null);
  }, [busy, chatWait]);

  // Stick to bottom only while the user is already following the latest messages.
  // Scrolling up to read history must not yank them back down.
  useEffect(() => {
    if (busy) stickToBottom.current = true;
  }, [busy]);

  const onThreadScroll = () => {
    const el = threadRef.current;
    if (!el) return;
    const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
    stickToBottom.current = dist < 96;
  };

  useEffect(() => {
    if (!stickToBottom.current) return;
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [visibleChat, traces, busy, lastThought]);

  return (
    <div className="agent-shell">
      <header className="agent-topbar">
        <div className="agent-topbar-left">
          <button type="button" className="icon-btn" onClick={onToggleSidebar} title="Toggle sidebar">
            {sidebarOpen ? <PanelLeftClose size={16} strokeWidth={1.5} /> : <PanelLeft size={16} strokeWidth={1.5} />}
          </button>
          <span className="project-name">{project.app_config.title}</span>
        </div>
        <div className="agent-topbar-actions">
          <button type="button" className="icon-btn mission-nav-button" onClick={() => { setMissionFocus("summary"); setMissionOpen(true); }} title="Mission details" aria-pressed={missionOpen}>
            <Flag size={16} strokeWidth={1.5} /><span>Mission</span>
          </button>
          <button type="button" className="icon-btn" onClick={() => { setMissionFocus("crew"); setMissionOpen(true); }} title="Mission crew" aria-pressed={missionOpen && missionFocus === "crew"}>
            <Users size={16} strokeWidth={1.5} />
          </button>
        </div>
      </header>

      <nav className="agent-workspace-tabs" aria-label="Mission workspace">
        <button type="button" className={workspaceTab === "chat" ? "active" : ""} onClick={() => setWorkspaceTab("chat")}><MessageSquare size={14} /> Chat</button>
        <button type="button" className={workspaceTab === "work" ? "active" : ""} onClick={() => setWorkspaceTab("work")}><ListChecks size={14} /> Work</button>
        <button type="button" className={workspaceTab === "files" ? "active" : ""} onClick={() => setWorkspaceTab("files")}><FileText size={14} /> Files{userFacingFiles(files).length ? <em>{userFacingFiles(files).length}</em> : null}</button>
      </nav>

      {error && (
        <div className="toast error-toast agent-toast" role="alert">
          <span>{error}</span>
          <button type="button" onClick={onDismissError}>
            ×
          </button>
        </div>
      )}

      <div className="mission-collab-body">
      {workspaceTab !== "work" ? <aside className="mission-crew-rail" aria-label="Mission crew">
        <header><span>AGENTS</span>{canCreateMissionAgents ? <button type="button" onClick={() => { setMissionFocus("crew"); setMissionOpen(true); }} title="Add or manage agents">+</button> : null}</header>
        {missionAgents.length ? <ul>{missionAgents.map((agent) => {
          const active = missionData?.runs.find((run) => run.current_agent_id === agent.id && run.status === "running");
          const queued = missionData?.runs.find((run) => run.status === "queued" && (run.assigned_agent_ids?.includes(agent.id) || !run.assigned_agent_ids?.length));
          return <li key={agent.id}><button type="button" className={mentionedAgentIds.includes(agent.id) ? "selected" : ""} onClick={() => setSelectedCrew({ kind: "agent", id: agent.id })} title={`Open ${agent.name}'s profile`}><i>{agent.name.slice(0, 1).toUpperCase()}</i><span><strong>{agent.name}</strong><small>{agent.role}</small></span><em className={active ? "working" : queued ? "queued" : "ready"} /></button><button type="button" className="mission-crew-mention" onClick={() => mentionAgent(agent.name)} title={`Assign work to ${agent.name}`}>@</button>{active || queued ? <small className="agent-work-state">{active ? "Working" : "Queued"}</small> : null}</li>;
        })}</ul> : canCreateMissionAgents ? <button type="button" className="mission-crew-empty" onClick={() => { setMissionFocus("crew"); setMissionOpen(true); }}><Users size={16} /><span>Add your first agent</span></button> : <div className="mission-crew-empty" aria-live="polite"><Users size={16} /><span>Waiting for a Mission owner to add an agent</span></div>}
		<div className="mission-human-list"><header><span>HUMANS</span>{canInviteMissionHumans ? <button type="button" onClick={() => setWorkspaceTab("work")} title="Invite a human">+</button> : null}</header>{humanMembers.length ? humanMembers.map((member) => { const live = roomData?.presence.find((item) => item.actor_id === member.actor_id); const name = member.display_name || "Teammate"; return <div className="mission-human-item" key={member.actor_id}><button type="button" className="mission-human-row" onClick={() => setSelectedCrew({ kind: "human", id: member.actor_id })}><i>{name.slice(0, 1).toUpperCase()}</i><span><strong>{name}</strong><small>{member.role} · {live?.status || "offline"}</small></span></button><button type="button" className="mission-human-mention" onClick={() => { const separator = input && !input.endsWith(" ") ? " " : ""; onInput(`${input}${separator}${agentHandle(name)} `); }} title={`Message ${name}`}>@</button></div>; }) : canInviteMissionHumans ? <button type="button" onClick={() => setWorkspaceTab("work")}>Invite collaborators</button> : <p className="mission-human-empty">No other humans have joined yet.</p>}</div>
        <section className="mission-task-queue"><header><span>ACTIVE WORK</span><strong>{missionData?.runs.filter((run) => ["queued", "running", "awaiting_approval"].includes(run.status)).length || 0}</strong></header>{missionData?.runs.filter((run) => ["queued", "running", "awaiting_approval"].includes(run.status)).slice(-4).reverse().map((run) => <button type="button" key={run.id} onClick={() => setWorkspaceTab("work")}><span>{run.trigger_snapshot?.note || "Mission run"}</span><small>{run.status.replaceAll("_", " ")}</small></button>)}</section>
      </aside> : null}
      <div className={`agent-center${workspaceTab !== "chat" ? " agent-center-surface" : ""}`}>
        {workspaceTab === "work" ? (
		  <ProjectRoomContainer projectId={project.id} onOpenChat={() => setWorkspaceTab("chat")} mission={missionData} onMissionRefresh={refreshMission} missionAssignments={(missionData?.runs ?? []).map((run) => ({ id: run.id, title: run.trigger_snapshot?.note || "Mission run", status: run.status, ownerNames: run.assigned_agent_ids?.length ? run.assigned_agent_ids.map((id) => missionAgents.find((agent) => agent.id === id)?.name || id) : missionAgents.map((agent) => agent.name), currentOwner: run.current_agent_id ? missionAgents.find((agent) => agent.id === run.current_agent_id)?.name : undefined }))} />
        ) : workspaceTab === "files" ? (
          <MissionFiles files={files} deliverables={missionData?.deliverables ?? []} onBack={() => setWorkspaceTab("chat")} />
        ) : (
          <>
        {missionData ? <OnboardingChecklist
          readiness={{
            sourceAdded: userFacingFiles(files).length > 0,
            crewReady: missionData.agents.length > 0,
            workPlanApproved: missionData.readiness?.graph.status === "approved",
          }}
          onAddSource={() => setWorkspaceTab("files")}
          onOpenCrew={() => { setMissionFocus("crew"); setMissionOpen(true); }}
          onReviewPlan={() => { setMissionFocus("summary"); setMissionOpen(true); }}
        /> : null}
        <div
          className="agent-thread cursor-thread"
          ref={threadRef}
          onScroll={onThreadScroll}
        >
          {visibleChat.length === 0 && !missionData?.runs.length && !busy ? (
            <div className="thread-first">
              <p>
                {isPlan
                  ? "Describe what you want — sources, research, or scope."
                  : "Describe what to change. Preview updates after each message."}
              </p>
            </div>
          ) : null}

          {visibleChat.map((m, i) => (
            <Fragment key={i}>
              {i === lastAssistantIdx && lastThought && !busy && chatWait ? (
                <div className="cursor-turn cursor-turn-thought">
                  <ThinkingTrail
                    events={lastThought.events}
                    live={false}
                    startedAt={lastThought.startedAt}
                    endedAt={lastThought.endedAt}
                  />
                </div>
              ) : null}
              <MessageTurn
                message={m}
                snapshot={snapshot}
                busy={busy}
                isLatestAssistant={i === lastAssistantIdx}
                onOpenPreview={onOpenPreview}
                actions={
                  i === lastAssistantIdx && !busy && turnKind(m) === "assistant" && m.content.trim() ? (
                    <TurnActions
                      text={m.content}
                      retryText={lastUserTextBefore(visibleChat, i)}
                      busy={busy}
                      onRetry={onRetry}
                    />
                  ) : undefined
                }
              />
            </Fragment>
          ))}

		  <CrewRecommendations recommendations={missionData?.crew_recommendations ?? []} adding={addingRecommendation} canCreate={canCreateMissionAgents} onAdd={addRecommendation} onCustomize={(recommendation) => { setCustomizingRecommendation(recommendation); setMissionFocus("crew"); setMissionOpen(true); }} />

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
                  chatWait
                    ? "Working on your message"
                    : jobKind === "iterate_run"
                      ? "Updating preview from your message"
                      : `Building your ${noun}`
                }
                jobKind={jobKind}
                jobStatus={busy ? "running" : "idle"}
                phase={project.phase}
                fileCount={userFacingFiles(files).length}
                traces={traces}
                startedAt={waitStartedAt}
                onStop={onCancel}
              />
            </div>
          ) : null}

          {!busy && isPlan && agentWantsBuild && onApprove ? (
            <div className="cursor-turn cursor-turn-approval">
              <ApprovalCard
                question={`Ready to scaffold your ${noun}?`}
                options={[
                  { id: "build", label: "Build", primary: true },
                  { id: "refine", label: "Keep refining" },
                ]}
                busy={busy}
                onChoose={(id) => {
                  if (id === "build") onApprove();
                  else onInput("Hold off on building — let's refine the plan first.");
                }}
                onDismiss={() => onInput("Hold off on building — let's refine the plan first.")}
              />
            </div>
          ) : null}

          <div ref={endRef} />
        </div>

        <SelectionActions
          disabled={busy}
          onAction={(action, text) => {
            onInput(selectionPrompt(action, text));
          }}
        />

        <div className="agent-composer-wrap">
          <div className="composer-chrome" role="toolbar" aria-label="Project actions">
            <div className="composer-chrome-meta">
              {stage ? (
                <span className={`chrome-chip${stage.cls === "source-prime" ? " ok" : ""}`} title={stage.text}>
                  {stage.text}
                </span>
              ) : null}
              <span className="chrome-chip" title={formatChipHint(project.artifact_kind)}>
                {formatChipLabel(project.artifact_kind)}
              </span>
              {needs?.detail ? (
                <span className="chrome-chip" title={needs.title || needs.detail}>
                  {needs.detail}
                </span>
              ) : null}
            </div>

            <div className="composer-chrome-actions">
              {hasPreview ? (
                <button
                  type="button"
                  className="composer-action"
                  onClick={onOpenPreview}
                  title="Open preview"
                >
                  <Globe size={14} strokeWidth={1.5} />
                  Preview
                </button>
              ) : null}
              {!isPlan && project.checkpoints?.length > 0 && onRollback ? (
                <VersionsMenu
                  versions={project.checkpoints as Checkpoint[]}
                  disabled={busy}
                  onRestore={(id) => onRollback(id)}
                />
              ) : null}
              {isPlan && onApprove ? (
                <button
                  type="button"
                  className={`composer-action emphasis${agentWantsBuild ? " pulse-build" : ""}`}
                  disabled={busy}
                  onClick={onApprove}
                  title={agentWantsBuild ? "Ready to scaffold" : buildLabel}
                >
                  Build
                  <ArrowRight size={14} strokeWidth={1.5} />
                </button>
              ) : null}
              {busy && onCancel ? (
                <button
                  type="button"
                  className="composer-action danger"
                  onClick={onCancel}
                  title="Stop current job"
                >
                  <Square size={10} fill="currentColor" />
                  Stop
                </button>
              ) : null}
              <ChromeMore
                sidebarOpen={sidebarOpen}
                busy={busy}
                onAccount={onGovernance}
                onRebuild={!isPlan ? onRebuild : undefined}
              />
            </div>
          </div>
          {assignmentError ? <div className="assignment-error" role="alert">{assignmentError}</div> : null}
          <PromptComposer
            value={input}
            onChange={onInput}
            onSubmit={onSend}
            onCancel={onCancel}
            disabled={project.status === "failed"}
            busy={busy}
            files={files}
            placeholder="Describe what to change…"
            submitLabel="Send"
            modeTag="Message"
            mentions={[]}
            asTask={false}
          />
        </div>
          </>
        )}
      </div>
      </div>
	  {missionOpen ? <MissionPod projectId={project.id} projectTitle={project.app_config.title} projectPrompt={project.prompt} artifactKind={project.artifact_kind} focus={missionFocus} initialAgent={customizingRecommendation} canCreateAgents={canCreateMissionAgents} onClose={() => { setMissionOpen(false); setCustomizingRecommendation(null); void refreshMission(); }} onOpenTasks={() => { setMissionOpen(false); setWorkspaceTab("work"); }} onOpenFiles={() => { setMissionOpen(false); setWorkspaceTab("files"); }} /> : null}
      {selectedCrew ? <CrewProfileDrawer selection={selectedCrew} mission={missionData} room={roomData} onClose={() => setSelectedCrew(null)} onAssign={(name) => { setSelectedCrew(null); mentionAgent(name); }} onOpenWork={() => { setSelectedCrew(null); setWorkspaceTab("work"); }} /> : null}
    </div>
  );
}

function CrewProfileDrawer({ selection, mission, room, onClose, onAssign, onOpenWork }: {
  selection: { kind: "agent" | "human"; id: string };
  mission: MissionOverview | null;
  room: Cmul8RoomPayload | null;
  onClose: () => void;
  onAssign: (name: string) => void;
  onOpenWork: () => void;
}) {
  const agent = selection.kind === "agent" ? mission?.agents.find((item) => item.id === selection.id) : undefined;
  const human = selection.kind === "human" ? room?.room.members.find((item) => item.actor_id === selection.id) : undefined;
  const name = agent?.name || human?.display_name || "Teammate";
  const current = agent ? mission?.runs.find((run) => run.current_agent_id === agent.id && run.status === "running") : undefined;
  const recent = agent ? (mission?.runs ?? []).filter((run) => run.assigned_agent_ids?.includes(agent.id) || run.current_agent_id === agent.id).slice(-4).reverse() : [];
  const guidance = agent?.autonomy === "assist" ? "Returns work for human review" : agent?.autonomy === "execute_safely" ? "Works inside its approved scope" : "Asks before consequential work";
  const scope = agent?.scope === "app" ? "Mission sources and the Mission app" : agent?.scope === "sources" ? "Mission sources" : "Mission sources and deliverables";
	return <div className="crew-profile-scrim" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}><aside className="crew-profile" aria-label={`${name} profile`}><header><div><i>{name.slice(0, 1).toUpperCase()}</i><span><small>{selection.kind === "agent" ? "MISSION AGENT" : "HUMAN TEAMMATE"}</small><h2>{name}</h2><p>{agent?.role || human?.role || "Member"}</p></span></div><button type="button" onClick={onClose} aria-label="Close profile">×</button></header><div className="crew-profile__body">{agent ? <><section className="crew-profile__status"><span className={current ? "working" : "ready"} /> <strong>{current ? "Working now" : "Ready"}</strong><small>{current?.trigger_snapshot?.note || "Available for an assignment"}</small></section><section><span>JOB</span><p>{agent.mandate || "This agent works toward the Mission outcome."}</p></section><div className="crew-profile__facts"><section><span>SCOPE</span><p>{scope || "Mission sources and deliverables"}</p></section><section><span>GUIDANCE</span><p>{guidance}</p></section></div>{recent.length ? <section><span>RECENT WORK</span><ul>{recent.map((run) => <li key={run.id}>{run.trigger_snapshot?.note || "Mission assignment"} <small>{run.status.replaceAll("_", " ")}</small></li>)}</ul></section> : null}</> : <><section><span>MISSION ROLE</span><p>{human?.role === "owner" || human?.role === "admin" ? "Can steer the Mission, manage the team, and make consequential decisions." : human?.role === "reviewer" || human?.role === "approver" ? "Reviews evidence and makes designated human decisions." : "Collaborates with the crew and follows Mission work."}</p></section><section><span>PRESENCE</span><p>See the crew rail for current availability.</p></section></>} </div><footer>{agent ? <button type="button" className="primary" onClick={() => onAssign(name)}>Assign with @{name.replace(/\s+/g, "_")}</button> : null}<button type="button" onClick={onOpenWork}>Open Work</button></footer></aside></div>;
}

function MissionFiles({ files, deliverables, onBack }: { files: DataRoomFile[]; deliverables: MissionOverview["deliverables"]; onBack: () => void }) {
  const sources = userFacingFiles(files);
	const renderFiles = (items: DataRoomFile[]) => <div className="mission-files-grid">{items.map((file) => <article key={file.name}><FileText size={18} /><div><strong>{file.name}</strong><span>{file.type} · {file.size < 1024 ? `${file.size} B` : `${(file.size / 1024).toFixed(1)} KB`}</span>{file.detail ? <p>{file.detail}</p> : null}</div></article>)}</div>;
	const renderOutputs = () => <div className="mission-files-grid">{deliverables.map((item) => <article key={item.id}><FileText size={18} /><div><strong>{item.name}</strong><span>Version {item.version} · {item.state.replaceAll("_", " ")}</span><p>{item.verified_at ? "Verified Mission output" : "Awaiting human verification"}</p></div></article>)}</div>;
  return <section className="mission-files-view" aria-labelledby="mission-files-title">
    <header><div><span>MISSION FILES</span><h1 id="mission-files-title">Shared evidence</h1><p>Files attached to this Mission stay available to its human-and-agent team.</p></div><button type="button" onClick={onBack}>Back to chat</button></header>
	<section className="mission-file-group"><h2>Sources</h2>{sources.length ? renderFiles(sources) : <p className="mission-files-empty-copy">No source files have been added yet.</p>}</section>
	<section className="mission-file-group"><h2>Outputs</h2>{deliverables.length ? renderOutputs() : <p className="mission-files-empty-copy">No Mission outputs have been produced yet.</p>}</section>
  </section>;
}
