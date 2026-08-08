import { ArrowUp, Database, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ArtifactKind, DataRoomFile, Project } from "../api";
import { GuestAuthGate } from "./GuestAuthGate";
import { SourcesPanel } from "./SourcesPanel";

/** Prompt chips — three shown; set rotates each UTC day. */
const IDEA_BANK = [
  "A vendor risk dashboard from my diligence pack",
  "A findings table ranked by severity",
  "An analytics app my team can explore",
  "A diligence room explorer with search and filters",
  "A risk score board for our top vendors",
  "An ops console that surfaces critical findings first",
  "A theme breakdown of issues across the data room",
  "A shareable risk report my partners can open",
  "A vendor scorecard with evidence links",
  "A compliance findings browser for the audit pack",
  "A heatmap of risk by region and owner",
  "A triage queue ordered by severity and score",
  "An internal briefing app for tomorrow's risk review",
  "A source inventory of what we ingested and why",
  "A comparison view of vendors by max risk score",
  "A compact command center for diligence follow-ups",
  "A findings explorer my analysts can query in chat",
  "A portfolio risk strip with drill-down to evidence",
  "A board deck on vendor risk from our diligence pack",
  "A one-page risk briefing for tomorrow's review",
];

const FORMAT_OPTIONS: { kind: ArtifactKind; label: string; hint: string }[] = [
  { kind: "data_app", label: "App", hint: "Interactive command center" },
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
  return p.status || p.phase;
}

function kindShort(p: Project): string {
  const k = p.artifact_kind || "data_app";
  return FORMAT_OPTIONS.find((f) => f.kind === k)?.label || "App";
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
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const projectsRef = useRef<HTMLElement>(null);
  const sourceCount = (dataAttached ? files.length : 0) + pendingFiles.length;
  // Guests may send with sample pack checked even before fixture list loads.
  const sourcesReady = sourceCount > 0 || (!authed && dataAttached);
  const canBuild = prompt.trim().length >= 3 && sourcesReady && !busy && !guestGateOpen;
  const recent = projects.slice(0, 12);
  const pills = useMemo(() => pillsForDay(utcDayIndex()), []);
  const activeFormat = FORMAT_OPTIONS.find((f) => f.kind === artifactKind) || FORMAT_OPTIONS[0]!;
  const gated = !authed && guestGateOpen;

  useEffect(() => {
    if (!gated) promptRef.current?.focus();
  }, [gated]);

  function scrollToProjects() {
    projectsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  return (
    <div className="landing">
      <img className="landing-hero-img" src="/images/hero-sky.jpg" alt="" aria-hidden />
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
          Describe what you need from the data room. Pick a format — we build it in one shot,
          then you chat to refine and ship.
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
              onClick={() => onArtifactKind(f.kind)}
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

        {!gated && (
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
            <ul className="landing-project-list">
              {recent.map((p) => (
                <li key={p.id}>
                  <button
                    type="button"
                    className="landing-project-item"
                    disabled={busy}
                    onClick={() => onOpenProject?.(p.id)}
                  >
                    <span className="landing-project-title">
                      {p.app_config?.title || p.goal || "Untitled"}
                    </span>
                    <span className="landing-project-meta">
                      {kindShort(p)} · {phaseLabel(p)}
                      {p.row_count ? ` · ${p.row_count} rows` : ""}
                    </span>
                  </button>
                </li>
              ))}
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
