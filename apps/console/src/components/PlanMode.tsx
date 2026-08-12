import { ArrowRight, Bot, CheckCircle2, Shield, User } from "lucide-react";
import { useEffect, useRef } from "react";
import type { DataRoomFile, Project, Snapshot } from "../api";
import { BuildSteps } from "./BuildSteps";
import { FileTypeIcon } from "./FileTypeIcon";
import { PromptComposer } from "./PromptComposer";

type Props = {
  snapshot: Snapshot;
  files: DataRoomFile[];
  input: string;
  busy: boolean;
  error: string | null;
  onInput: (v: string) => void;
  onSend: () => void;
  onApprove: () => void;
  onGovernance: () => void;
  onDismissError: () => void;
};

function formatTime(at?: string) {
  if (!at) return "";
  try {
    return new Date(at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

export function PlanMode({
  snapshot,
  files,
  input,
  busy,
  error,
  onInput,
  onSend,
  onApprove,
  onGovernance,
  onDismissError,
}: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const project = snapshot.project;
  const preview = project.plan_preview;

  useEffect(() => {
    if (busy) stickToBottom.current = true;
  }, [busy]);

  useEffect(() => {
    if (!stickToBottom.current) return;
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [project.chat, busy]);

  return (
    <div className="plan-shell">
      <header className="plan-header">
        <div className="plan-header-left">
          <span className="product">Simulacra</span>
          <span className="sep">/</span>
          <span className="plan-badge">Plan mode</span>
          <span className="project-name">{project.app_config.title}</span>
        </div>
        <div className="plan-header-right">
          <button type="button" className="link-btn" onClick={onGovernance}>
            Governance
          </button>
          <button type="button" className="approve-btn" disabled={busy} onClick={onApprove}>
            Approve & Build
            <ArrowRight size={14} />
          </button>
        </div>
      </header>

      <div className="integration-banner">
        <Shield size={16} />
        <div>
          <strong>Apps never access business systems directly.</strong>
          <p>
            Simulacra is your integration control layer — data flows through governed APIs with
            authentication, audit logging, and eval gates.
          </p>
        </div>
      </div>

      {error && (
        <div className="toast error-toast plan-toast" role="alert">
          <span>{error}</span>
          <button type="button" onClick={onDismissError}>×</button>
        </div>
      )}

      <div className="plan-body">
        <section className="plan-chat">
          <div className="thread">
            {project.chat.map((m, i) => (
              <article key={i} className={`bubble ${m.role}`}>
                <div className="bubble-head">
                  <span className="avatar">{m.role === "user" ? <User size={12} /> : <Bot size={12} />}</span>
                  <span className="who">{m.role === "user" ? "You" : "Simulacra"}</span>
                  <time>{formatTime(m.at)}</time>
                </div>
                <div className="bubble-body plan-md">{m.content}</div>
              </article>
            ))}
            {busy && (
              <article className="bubble assistant">
                <BuildSteps active />
              </article>
            )}
            <div ref={endRef} />
          </div>
          <PromptComposer
            value={input}
            onChange={onInput}
            onSubmit={onSend}
            disabled={busy}
            busy={busy}
            files={files}
            placeholder="Explore the data room, tag sources with @, refine requirements…"
            submitLabel="Plan"
            modeTag="Plan"
          />
        </section>

        <aside className="plan-explorer">
          <h3>Data room</h3>
          <p className="explorer-note">Read-only · {preview?.row_count ?? 0} rows extracted</p>
          <ul className="explorer-files">
            {files.map((f) => (
              <li key={f.name}>
                <FileTypeIcon ext={f.type} />
                <span>{f.name}</span>
              </li>
            ))}
          </ul>

          {preview && preview.vendors?.length > 0 && (
            <>
              <h4>Entities</h4>
              <div className="vendor-chips">
                {preview.vendors.map((v) => (
                  <span key={v} className="vendor-chip">{v}</span>
                ))}
              </div>
            </>
          )}

          {preview?.sample_rows?.length > 0 && (
            <>
              <h4>Sample rows</h4>
              <div className="sample-table-wrap">
                <table className="sample-table">
                  <thead>
                    <tr>
                      {Object.keys(preview.sample_rows[0] || {})
                        .slice(0, 4)
                        .map((col) => (
                          <th key={col}>{col}</th>
                        ))}
                    </tr>
                  </thead>
                  <tbody>
                    {preview.sample_rows.map((row, i) => (
                      <tr key={i}>
                        {Object.keys(preview.sample_rows[0] || {})
                          .slice(0, 4)
                          .map((col) => (
                            <td key={col}>{String(row[col] ?? "")}</td>
                          ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          <div className="plan-summary-card">
            <CheckCircle2 size={14} />
            <div>
              <strong>Proposed app</strong>
              <p>{project.app_config.title}</p>
              <span>{project.app_config.subtitle}</span>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
