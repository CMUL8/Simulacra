/**
 * Streaming / final answer block — prose only.
 */
import type { ReactNode } from "react";

type Props = {
  children: ReactNode;
  streaming?: boolean;
};

export function AnswerBlock({ children, streaming }: Props) {
  return (
    <div className={`bui-answer${streaming ? " streaming" : ""}`}>
      <div className="bui-answer-body">{children}</div>
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
