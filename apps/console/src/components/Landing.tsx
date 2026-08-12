import { ArrowUp, Database, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ArtifactKind, DataRoomFile, Project } from "../api";
import { BG_IMAGES, bgPresetFromSearch } from "../bgPreset";
import { GuestAuthGate } from "./GuestAuthGate";
import { SourcesPanel } from "./SourcesPanel";

/** Prompt chips — three shown; set rotates each UTC day. */
const IDEA_BANK = [
  "An analytics app my team can explore",
  "A shareable research report my partners can open",
  "A board deck from our latest research pack",
  "A one-page briefing for tomorrow's review",
  "An internal briefing app for the weekly review",
  "A source inventory of what we ingested and why",
  "A comparison view ranked by the key metric",
  "A compact explorer with search and filters",
  "A narrative report on the topic in the data room",
  "A slide deck that tells the story in six slides",
  "A one-pager with the headline numbers only",
  "A data table my analysts can query in chat",
];

const FORMAT_OPTIONS: { kind: ArtifactKind; label: string; hint: string }[] = [
  { kind: "data_app", label: "App", hint: "Interactive explorer" },
  { kind: "report", label: "Report", hint: "Long-form HTML document" },
  { kind: "slides", label: "Slides", hint: "Multi-page deck" },
  { kind: "one_pager", label: "One-pager", hint: "Single printable sheet" },
];

function utcDayIndex(): number {
  return Math.floor(Date.now() / 86_400_000);
}

function pillsForDay(day: number, count = 3): string[] {
  const n = IDEA_BANK.length;
  const start = ((day % n) + n) % n;
  const step = Math.max(1, Math.floor(n / count));
  const out: string[] = [];
  const used = new Set<number>();
  for (let i = 0; out.length < count && i < n * 2; i++) {
    const idx = (start + i * step) % n;
    if (used.has(idx)) continue;
    used.add(idx);
    out.push(IDEA_BANK[idx]!);
  }
  return out;
}

function phaseLabel(p: Project): string {
  if (p.deployed) return "Shipped";
  if (p.phase === "ready") return "Built";
  if (p.phase === "plan") return "Draft";
  const status = (p.status || "").toLowerCase();
  if (status === "building_app" || status === "publishing_preview" || status === "approved") {
    return "Building";
  }
  if (status === "extracting" || status === "gating") return "Scanning";
  if (status === "planning") return "Draft";
  if (status === "failed") return "Failed";
  if (status === "draft" || status === "ready" || status === "deployed") {
    return status === "deployed" ? "Shipped" : status === "ready" ? "Built" : "Draft";
  }
  // Never leak snake_case internals into the UI
  if (status.includes("_")) return p.phase === "build" ? "Building" : "Draft";
  return p.phase === "build" ? "Building" : p.phase || "Draft";
}

function kindShort(p: Project): string {
  const k = p.artifact_kind || "data_app";
  return FORMAT_OPTIONS.find((f) => f.kind === k)?.label || "App";
}

function kindKey(p: Project): string {
  const k = p.artifact_kind || "data_app";
  return FORMAT_OPTIONS.some((f) => f.kind === k) ? k : "data_app";
}

const STOCK_TITLES = new Set([
  "Vendor Risk Command Center",
  "Vendor Risk Dashboard",
  "Data Explorer",
  "Data App",
]);

function projectCardTitle(p: Project): string {
  const title = (p.app_config?.title || "").trim();
  const prompt = (p.prompt || "").trim();
  const product = (p.design_brief?.product_name || "").trim();
  const clean = (raw: string) =>
    raw
      .replace(
        /^(?:please\s+)?(?:write|create|make|build|generate|draft)\s+(?:me\s+)?(?:a|an|the)\s+(?:short\s+|quick\s+)?(?:report|memo|deck|slides?|app|dashboard|one[- ]?pager|brief)?\s*(?:about|on|for|covering)?\s*/i,
        "",
      )
      .replace(/^./, (c) => c.toUpperCase())
      .slice(0, 72)
      .trim();
  if (title && !STOCK_TITLES.has(title)) {
    const t = clean(title);
    if (t.length > 3) return t;
    return title;
  }
  if (product && !STOCK_TITLES.has(product) && product.length > 3) return clean(product) || product;
  if (prompt) {
    const line = clean(prompt.split("\n")[0]!.trim());
    if (line) return line;
  }
  return title || p.goal || "Untitled";
}

function projectCardSummary(p: Project): string {
  const sub = (p.app_config?.subtitle || "").trim();
  if (sub && !["Built from your sources", "Data Explorer", "Chat with the agent — Build when ready"].includes(sub)) {
    return sub.slice(0, 96);
  }
  const one = (p.design_brief?.one_liner || "").trim();
  if (one && one !== sub) return one.slice(0, 96);
  const prompt = (p.prompt || "").trim();
  const title = projectCardTitle(p);
  if (prompt && prompt !== title) {
    const rest = prompt.length > title.length && prompt.startsWith(title) ? prompt.slice(title.length).trim() : prompt;
    if (rest && rest !== title) return rest.slice(0, 96);
  }
  return "";
}

function relativeWhen(iso?: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.max(0, Math.round((Date.now() - t) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  const days = Math.round(hrs / 24);
  if (days < 14) return `${days}d ago`;
  return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

type Props = {
  prompt: string;
  artifactKind: ArtifactKind;
  busy: boolean;
  files: DataRoomFile[];
  pendingFiles?: File[];
  dataAttached: boolean;
  error: string | null;
  authed?: boolean;
  projects?: Project[];
  guestGateOpen?: boolean;
  clerkEnabled?: boolean;
  onPrompt: (v: string) => void;
  onArtifactKind: (k: ArtifactKind) => void;
  onToggleData: () => void;
  onPickPending?: (files: File[]) => void;
  onClearPending?: (name: string) => void;
  onBuild: () => void;
  onOpenProject?: (id: string) => void;
  onLogin?: () => void;
  onGuestCreateAccount?: () => void;
  onGuestSignIn?: () => void;
  onGuestGateDismiss?: () => void;
  onDismissError: () => void;
};

export function Landing({
  prompt,
  artifactKind,
  busy,
  files,
  pendingFiles = [],
  dataAttached,
  error,
  authed = true,
  projects = [],
  guestGateOpen = false,
  clerkEnabled = false,
  onPrompt,
  onArtifactKind,
  onToggleData,
  onPickPending,
  onClearPending,
  onBuild,
  onOpenProject,
  onLogin,
  onGuestCreateAccount,
  onGuestSignIn,
  onGuestGateDismiss,
  onDismissError,
}: Props) {
  const [formatTouched, setFormatTouched] = useState(false);
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const [bgPreset] = useState(bgPresetFromSearch);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const landingRef = useRef<HTMLDivElement>(null);
  const projectsRef = useRef<HTMLElement>(null);
  const sourceCount = (dataAttached ? files.length : 0) + pendingFiles.length;
  // Prompt alone is enough — empty/mismatched sources open plan chat to ask for data.
  const canBuild = prompt.trim().length >= 3 && !busy && !guestGateOpen;
  const recent = projects.slice(0, 12);
  const pills = useMemo(() => pillsForDay(utcDayIndex()), []);
  const activeFormat = FORMAT_OPTIONS.find((f) => f.kind === artifactKind) || FORMAT_OPTIONS[0]!;
  const gated = !authed && guestGateOpen;

  useEffect(() => {
    if (!gated) promptRef.current?.focus();
  }, [gated]);

  // Soft-switch format from prompt language unless the user already picked one.
  useEffect(() => {
    if (formatTouched) return;
    const lower = prompt.toLowerCase();
    let next: ArtifactKind | null = null;
    if (/\b(slide deck|board deck|presentation|powerpoint|keynote|slides)\b/.test(lower)) next = "slides";
    else if (/\b(one[- ]?pager|one[- ]?page|single page brief|1-pager)\b/.test(lower)) next = "one_pager";
    else if (/\b(report|memo|write-up|long-form|narrative brief)\b/.test(lower)) next = "report";
    else if (/\b(dashboard|command center|explorer|ops console|data app)\b/.test(lower)) next = "data_app";
    if (next && next !== artifactKind) onArtifactKind(next);
  }, [prompt, formatTouched, artifactKind, onArtifactKind]);

  function scrollToProjects() {
    const root = landingRef.current;
    const section = projectsRef.current;
    if (!root || !section) return;
    // Scroll the landing pane itself — window scrollIntoView fights overflow + nested lists.
    const navClearance = 96;
    const top = Math.max(0, section.offsetTop - navClearance);
    root.scrollTo({ top, behavior: "smooth" });
  }

  return (
    <div className="landing" ref={landingRef} data-bg={bgPreset}>
      <img className="landing-hero-img" src={BG_IMAGES[bgPreset]} alt="" aria-hidden />
      <div className="landing-hero-veil" aria-hidden />

      <header className="landing-nav">
        <div className="landing-nav-pill">
          <span className="landing-nav-brand">
            Simu<em>lacra</em>
          </span>

          <nav className="landing-nav-links" aria-label="Primary">
            {authed && recent.length > 0 && (
              <button type="button" onClick={scrollToProjects}>
                Projects
              </button>
            )}
            <button type="button" onClick={() => onLogin?.()}>
              {authed ? "Account" : "Login"}
            </button>
          </nav>

          <button
            type="button"
            className="landing-nav-cta"
            onClick={() => (authed ? promptRef.current?.focus() : onLogin?.())}
          >
            {authed ? "Start building" : "Get started"}
          </button>
        </div>
      </header>

      <div className="landing-content" id="start">
        <h1 className="brand-mark">
          Simu<em>lacra</em>
        </h1>
        <p className="landing-sub">
          Describe what you need. Pick a format — the agent opens in chat to steer sources
          and scope, then you Build, refine, and Ship.
        </p>

        {error && (
          <div className="landing-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={onDismissError} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        )}

        <div className="format-strip" role="group" aria-label="Output format">
          {FORMAT_OPTIONS.map((f) => (
            <button
              key={f.kind}
              type="button"
              className={f.kind === artifactKind ? "format-chip on" : "format-chip"}
              disabled={busy || gated}
              title={f.hint}
              onClick={() => {
                setFormatTouched(true);
                onArtifactKind(f.kind);
              }}
            >
              {f.label}
            </button>
          ))}
        </div>

        <div className={`anything-prompt${gated ? " gated" : ""}`}>
          <textarea
            ref={promptRef}
            value={prompt}
            onChange={(e) => onPrompt(e.target.value)}
            placeholder={activeFormat.hint}
            rows={4}
            disabled={busy || gated}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canBuild) onBuild();
            }}
          />
          <div className="prompt-footer">
            <button
              type="button"
              className={`data-chip ${sourceCount > 0 || (!authed && dataAttached) ? "on" : ""}`}
              onClick={() => setSourcesOpen(true)}
              disabled={busy || gated}
              title="Manage sources"
            >
              <Database size={15} strokeWidth={1.75} />
              <span>
                {sourceCount > 0
                  ? `Sources · ${sourceCount}`
                  : !authed && dataAttached
                    ? "Sources · sample"
                    : "Add sources"}
              </span>
            </button>
            <button
              type="button"
              className="send-orb"
              disabled={!canBuild}
              onClick={onBuild}
              aria-label="Start planning"
            >
              <ArrowUp size={18} />
            </button>
          </div>
        </div>

        {busy && (
          <p className="landing-busy-status" role="status" aria-live="polite" aria-busy="true">
            Starting your {activeFormat.label.toLowerCase()}
            <span className="landing-busy-dots" aria-hidden>
              <i />
              <i />
              <i />
            </span>
          </p>
        )}

        {gated && (
          <GuestAuthGate
            prompt={prompt}
            artifactKind={artifactKind}
            clerkEnabled={clerkEnabled}
            onCreateAccount={() => onGuestCreateAccount?.()}
            onSignIn={() => onGuestSignIn?.()}
            onEdit={() => onGuestGateDismiss?.()}
          />
        )}

        {!gated && !busy && (
          <div className="landing-pills">
            {pills.map((p) => (
              <button key={p} type="button" onClick={() => onPrompt(p)} disabled={busy}>
                {p}
              </button>
            ))}
          </div>
        )}

        {authed && recent.length > 0 && (
          <section className="landing-projects" ref={projectsRef} id="projects" aria-label="Your projects">
            <div className="landing-projects-head">
              <h2>Your projects</h2>
              <span>{projects.length}</span>
            </div>
            <ul className="landing-project-grid">
              {recent.map((p) => {
                const kind = kindKey(p);
                const title = projectCardTitle(p);
                const summary = projectCardSummary(p);
                const when = relativeWhen(p.created_at);
                return (
                  <li key={p.id}>
                    <button
                      type="button"
                      className={`landing-project-card kind-${kind}`}
                      disabled={busy}
                      onClick={() => onOpenProject?.(p.id)}
                    >
                      <span className="landing-project-card-top">
                        <span className="landing-project-kind">{kindShort(p)}</span>
                        <span className={`landing-project-phase phase-${p.deployed ? "shipped" : p.phase}`}>
                          {phaseLabel(p)}
                        </span>
                      </span>
                      <span className="landing-project-title">{title}</span>
                      {summary ? <span className="landing-project-summary">{summary}</span> : null}
                      <span className="landing-project-foot">
                        {p.row_count ? <span>{p.row_count.toLocaleString()} rows</span> : <span>No rows yet</span>}
                        {when ? <span>{when}</span> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
      </div>

      <SourcesPanel
        open={sourcesOpen}
        busy={busy}
        mode="landing"
        files={dataAttached ? files : []}
        pendingFiles={pendingFiles}
        fixtureAttached={dataAttached}
        onClose={() => setSourcesOpen(false)}
        onToggleFixture={onToggleData}
        onPickFiles={onPickPending}
        onClearPending={onClearPending}
      />
    </div>
  );
}

export { IDEA_BANK, FORMAT_OPTIONS, pillsForDay, utcDayIndex };
