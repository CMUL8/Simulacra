import { ArrowUp, Database, Sparkles, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { DataRoomFile, DesignBrief } from "../api";
import { BuildSteps } from "./BuildSteps";
import { DesignBriefForm } from "./DesignBriefForm";
import { FileTypeIcon } from "./FileTypeIcon";

const PILLS = [
  "A vendor risk dashboard from diligence files",
  "A filterable findings table ranked by severity",
  "An internal analytics app people can explore",
];

type Props = {
  goal: string;
  prompt: string;
  busy: boolean;
  files: DataRoomFile[];
  dataAttached: boolean;
  error: string | null;
  designBrief: DesignBrief;
  onGoal: (v: string) => void;
  onPrompt: (v: string) => void;
  onDesignBrief: (v: DesignBrief) => void;
  onToggleData: () => void;
  onBuild: () => void;
  onDismissError: () => void;
};

export function Landing({
  goal,
  prompt,
  busy,
  files,
  dataAttached,
  error,
  designBrief,
  onGoal,
  onPrompt,
  onDesignBrief,
  onToggleData,
  onBuild,
  onDismissError,
}: Props) {
  const [dataOpen, setDataOpen] = useState(false);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const canBuild = prompt.trim().length >= 3 && dataAttached && !busy;

  useEffect(() => {
    promptRef.current?.focus();
  }, []);

  function applyPill(text: string) {
    onPrompt(text);
    promptRef.current?.focus();
  }

  return (
    <div className="landing">
      <div className="landing-bg" />
      <div className="landing-vignette" />

      <div className="landing-sparkle" aria-hidden>
        <Sparkles size={18} />
      </div>

      <div className="landing-content">
        <h1>Start building a data app</h1>
        <p className="landing-sub">Plan first — explore your data room, then approve to build.</p>

        {error && (
          <div className="landing-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={onDismissError} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        )}

        <div className="prompt-card">
          <div className="prompt-section goal-section">
            <label htmlFor="goal">Goal</label>
            <input
              id="goal"
              type="text"
              value={goal}
              onChange={(e) => onGoal(e.target.value)}
              placeholder="What should this app achieve for its audience?"
              disabled={busy}
            />
          </div>

          <div className="prompt-divider" />

          <div className="prompt-section main-section">
            <textarea
              ref={promptRef}
              value={prompt}
              onChange={(e) => onPrompt(e.target.value)}
              placeholder="Describe what people should see, explore, and understand from your data…"
              disabled={busy}
              rows={3}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canBuild) {
                  e.preventDefault();
                  onBuild();
                }
              }}
            />
          </div>

          <DesignBriefForm value={designBrief} onChange={onDesignBrief} disabled={busy} />

          <div className="prompt-footer">
            <div className="data-attach-wrap">
              <button
                type="button"
                className={`data-btn ${dataAttached ? "attached" : ""}`}
                onClick={() => setDataOpen((v) => !v)}
                disabled={busy}
              >
                <Database size={14} />
                Data
                {dataAttached && <span className="data-count">{files.length}</span>}
              </button>

              {dataOpen && (
                <div className="data-popover">
                  <div className="data-popover-head">
                    <span>Data room</span>
                    <button type="button" className="data-close" onClick={() => setDataOpen(false)}>
                      <X size={12} />
                    </button>
                  </div>
                  <ul>
                    {files.map((f) => (
                      <li key={f.name}>
                        <FileTypeIcon ext={f.type} />
                        <span>{f.name}</span>
                      </li>
                    ))}
                  </ul>
                  <button
                    type="button"
                    className={`data-toggle ${dataAttached ? "on" : ""}`}
                    onClick={onToggleData}
                  >
                    {dataAttached ? "Attached" : "Attach data room"}
                  </button>
                  <p className="data-note">Fixture diligence room for this demo.</p>
                </div>
              )}
            </div>

            {busy ? (
              <div className="building-inline">
                <BuildSteps active />
              </div>
            ) : (
              <button type="button" className="start-btn" disabled={!canBuild} onClick={onBuild}>
                Start planning
                <ArrowUp size={14} strokeWidth={2.5} />
              </button>
            )}
          </div>
        </div>

        <div className="landing-pills">
          {PILLS.map((pill) => (
            <button key={pill} type="button" className="pill" disabled={busy} onClick={() => applyPill(pill)}>
              {pill}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
