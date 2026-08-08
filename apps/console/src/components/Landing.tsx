import { ArrowUp, Database, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { DataRoomFile } from "../api";
import { FileTypeIcon } from "./FileTypeIcon";

const PILLS = [
  "A vendor risk dashboard from my diligence pack",
  "A findings table ranked by severity",
  "An analytics app my team can explore",
];

type Props = {
  prompt: string;
  busy: boolean;
  files: DataRoomFile[];
  dataAttached: boolean;
  error: string | null;
  authed?: boolean;
  onPrompt: (v: string) => void;
  onToggleData: () => void;
  onBuild: () => void;
  onLogin?: () => void;
  onDismissError: () => void;
};

export function Landing({
  prompt,
  busy,
  files,
  dataAttached,
  error,
  authed = true,
  onPrompt,
  onToggleData,
  onBuild,
  onLogin,
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
      <img
        className="landing-hero-img"
        src="/images/hero-sky.jpg"
        alt=""
        aria-hidden
      />
      <div className="landing-hero-veil" aria-hidden />

      <header className="landing-nav">
        <div className="landing-nav-pill">
          <span className="landing-nav-brand">
            Simu<em>lacra</em>
          </span>
          <nav className="landing-nav-links" aria-label="Primary">
            <a href="#start">Product</a>
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
          Describe the internal app you need. Simulacra plans it, builds it with real code,
          and keeps every step auditable.
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
              className={`data-chip ${dataAttached ? "on" : ""}`}
              onClick={() => {
                onToggleData();
                setDataOpen((v) => !v);
              }}
              disabled={busy}
              title={dataAttached ? "Sources attached" : "Attach sources"}
            >
              <Database size={15} strokeWidth={1.75} />
              <span>{dataAttached ? `Sources · ${files.length || 0}` : "Add sources"}</span>
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
      </div>
    </div>
  );
}
