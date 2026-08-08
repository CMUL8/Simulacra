import { ArrowUp, Database, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { DataRoomFile, DesignBrief } from "../api";
import { DesignBriefForm } from "./DesignBriefForm";
import { FileTypeIcon } from "./FileTypeIcon";

const PILLS = [
  "Vendor risk command center from diligence files",
  "Filterable findings table ranked by severity",
  "Internal analytics app people can explore",
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

  return (
    <div className="landing">
      <div className="landing-bg" aria-hidden />
      <div className="landing-grain" aria-hidden />

      <div className="landing-content">
        <p className="brand-mark">
          Simu<em>lacra</em>
        </p>
        <p className="landing-sub">
          Turn data rooms into governed internal apps — plan, approve, build, audit.
        </p>

        {error && (
          <div className="landing-error" role="alert">
            <span>{error}</span>
            <button type="button" onClick={onDismissError} aria-label="Dismiss">
              <X size={14} />
            </button>
          </div>
        )}

        <div className="prompt-card anything-prompt">
          <div className="prompt-section goal-section">
            <label htmlFor="goal">Goal</label>
            <input
              id="goal"
              type="text"
              value={goal}
              onChange={(e) => onGoal(e.target.value)}
              placeholder="What should this app achieve?"
              disabled={busy}
            />
          </div>
          <div className="prompt-divider" />
          <div className="prompt-section main-section">
            <textarea
              ref={promptRef}
              value={prompt}
              onChange={(e) => onPrompt(e.target.value)}
              placeholder="Describe what you're building…"
              rows={3}
              disabled={busy}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && canBuild) onBuild();
              }}
            />
            <div className="prompt-footer">
              <button
                type="button"
                className={`data-chip ${dataAttached ? "on" : ""}`}
                onClick={() => {
                  onToggleData();
                  setDataOpen((v) => !v);
                }}
              >
                <Database size={14} />
                Data room {files.length ? `(${files.length})` : ""}
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
        </div>

        <div className="landing-pills">
          {PILLS.map((p) => (
            <button key={p} type="button" onClick={() => onPrompt(p)} disabled={busy}>
              {p}
            </button>
          ))}
        </div>

        {dataOpen && (
          <div className="landing-files">
            {files.map((f) => (
              <div key={f.name} className="landing-file">
                <FileTypeIcon ext={f.type || f.name.split(".").pop() || "txt"} />
                <span>{f.name}</span>
              </div>
            ))}
          </div>
        )}

        <details className="landing-brief">
          <summary>Look &amp; feel</summary>
          <DesignBriefForm value={designBrief} onChange={onDesignBrief} disabled={busy} />
        </details>
      </div>
    </div>
  );
}
