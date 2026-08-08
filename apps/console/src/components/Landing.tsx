import { ArrowUp, Database, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { DataRoomFile, Project } from "../api";
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

type Props = {
  prompt: string;
  busy: boolean;
  files: DataRoomFile[];
  pendingFiles?: File[];
  dataAttached: boolean;
  error: string | null;
  authed?: boolean;
  projects?: Project[];
  onPrompt: (v: string) => void;
  onToggleData: () => void;
  onPickPending?: (files: File[]) => void;
  onClearPending?: (name: string) => void;
  onBuild: () => void;
  onOpenProject?: (id: string) => void;
  onLogin?: () => void;
  onDismissError: () => void;
};

export function Landing({
  prompt,
  busy,
  files,
  pendingFiles = [],
  dataAttached,
  error,
  authed = true,
  projects = [],
  onPrompt,
  onToggleData,
  onPickPending,
  onClearPending,
  onBuild,
  onOpenProject,
  onLogin,
  onDismissError,
}: Props) {
  const [sourcesOpen, setSourcesOpen] = useState(false);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const projectsRef = useRef<HTMLElement>(null);
  const sourceCount = (dataAttached ? files.length : 0) + pendingFiles.length;
  const canBuild = prompt.trim().length >= 3 && sourceCount > 0 && !busy;
  const recent = projects.slice(0, 12);
  const pills = useMemo(() => pillsForDay(utcDayIndex()), []);

  useEffect(() => {
    promptRef.current?.focus();
  }, []);

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
          Describe the internal app you need. Simulacra plans it, builds it with real code, and keeps
          every step auditable.
        </p>

        {error && (
          <div className="landing-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={onDismissError} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        )}

        <div className="anything-prompt">
          <textarea
            ref={promptRef}
            value={prompt}
            onChange={(e) => onPrompt(e.target.value)}
            placeholder="What are you working on?"
            rows={4}
            disabled={busy}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canBuild) onBuild();
            }}
          />
          <div className="prompt-footer">
            <button
              type="button"
              className={`data-chip ${sourceCount > 0 ? "on" : ""}`}
              onClick={() => setSourcesOpen(true)}
              disabled={busy}
              title="Manage data sources"
            >
              <Database size={15} strokeWidth={1.75} />
              <span>{sourceCount > 0 ? `Sources · ${sourceCount}` : "Add sources"}</span>
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

        <div className="landing-pills">
          {pills.map((p) => (
            <button key={p} type="button" onClick={() => onPrompt(p)} disabled={busy}>
              {p}
            </button>
          ))}
        </div>

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
                      {phaseLabel(p)}
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

export { IDEA_BANK, pillsForDay, utcDayIndex };
