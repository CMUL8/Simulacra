/**
 * Streaming / final answer block:
 * prose + compact source cites + short follow-ups (Perplexity / Cursor density).
 */
import { FileText } from "lucide-react";
import type { ReactNode } from "react";

export type AnswerSource = { id: string; label: string };

type Props = {
  children: ReactNode;
  sources?: AnswerSource[];
  followUps?: string[];
  onFollowUp?: (text: string) => void;
  streaming?: boolean;
};

function citeLabel(label: string, index: number): string {
  const word = (label.split(/\s+/)[0] || label).trim();
  if (!word || word.length > 12) return String(index + 1);
  return word;
}

export function AnswerBlock({ children, sources = [], followUps = [], onFollowUp, streaming }: Props) {
  return (
    <div className={`bui-answer${streaming ? " streaming" : ""}`}>
      <div className="bui-answer-body">{children}</div>
      {sources.length > 0 ? (
        <div className="bui-answer-sources" aria-label={`${sources.length} sources`}>
          <div className="bui-answer-source-row">
            {sources.slice(0, 6).map((s, i) => (
              <span key={s.id} className="bui-source-cite" title={s.label}>
                <FileText size={11} aria-hidden />
                <em>{citeLabel(s.label, i)}</em>
              </span>
            ))}
            {sources.length > 6 ? (
              <span className="bui-source-cite more" title={`${sources.length - 6} more`}>
                +{sources.length - 6}
              </span>
            ) : null}
          </div>
        </div>
      ) : null}
      {followUps.length > 0 && onFollowUp ? (
        <div className="bui-followups" aria-label="Suggested next steps">
          {followUps.map((f) => (
            <button key={f} type="button" className="bui-followup" onClick={() => onFollowUp(f)}>
              {f}
            </button>
          ))}
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
