/**
 * Streaming / final answer block (Beautiful UI 03):
 * prose + inline source chips + follow-up actions.
 */
import type { ReactNode } from "react";

export type AnswerSource = { id: string; label: string };

type Props = {
  children: ReactNode;
  sources?: AnswerSource[];
  followUps?: string[];
  onFollowUp?: (text: string) => void;
  streaming?: boolean;
};

export function AnswerBlock({ children, sources = [], followUps = [], onFollowUp, streaming }: Props) {
  return (
    <div className={`bui-answer${streaming ? " streaming" : ""}`}>
      <div className="bui-answer-body">{children}</div>
      {sources.length > 0 ? (
        <div className="bui-answer-sources" aria-label="Sources">
          <span className="bui-answer-sources-count">
            {sources.length} source{sources.length === 1 ? "" : "s"}
          </span>
          <div className="bui-answer-source-row">
            {sources.slice(0, 8).map((s) => (
              <span key={s.id} className="bui-source-chip" title={s.label}>
                {s.label}
              </span>
            ))}
          </div>
        </div>
      ) : null}
      {followUps.length > 0 && onFollowUp ? (
        <div className="bui-followups" aria-label="Follow-ups">
          <span className="bui-followups-label">Follow-ups</span>
          <div className="bui-followups-row">
            {followUps.map((f) => (
              <button key={f} type="button" className="bui-followup" onClick={() => onFollowUp(f)}>
                {f}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function humanSourceLabel(name: string): string {
  const base = name.split("/").pop() || name;
  const stem = base
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/^\d+[_\-\s]*/, "")
    .replace(/[_-]+/g, " ")
    .trim();
  return stem ? stem.charAt(0).toUpperCase() + stem.slice(1) : "Source";
}
