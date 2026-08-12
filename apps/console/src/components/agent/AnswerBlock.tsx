/**
 * Streaming / final answer block — prose + optional follow-ups when idle.
 */
import type { ReactNode } from "react";

type Props = {
  children: ReactNode;
  followUps?: string[];
  onFollowUp?: (text: string) => void;
  streaming?: boolean;
};

export function AnswerBlock({ children, followUps = [], onFollowUp, streaming }: Props) {
  return (
    <div className={`bui-answer${streaming ? " streaming" : ""}`}>
      <div className="bui-answer-body">{children}</div>
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
