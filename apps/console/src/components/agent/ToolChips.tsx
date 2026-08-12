/** Tool chips — compact tool/call pills (Beautiful UI 05). */
import type { AgentEvent } from "../../api";

type Props = {
  events: AgentEvent[];
  limit?: number;
};

function chipLabel(e: AgentEvent): string {
  const label = (e.label || "").trim();
  if (label) return label.length > 42 ? `${label.slice(0, 40)}…` : label;
  const tool = String(e.meta?.tool || "tool");
  return tool.replace(/_/g, " ");
}

export function ToolChips({ events, limit = 6 }: Props) {
  const chips = events
    .filter((e) => e.type === "tool" || e.type === "phase")
    .filter((e) => (e.label || "").trim().length > 0)
    .slice(-limit);

  if (!chips.length) return null;

  return (
    <div className="bui-tool-chips" aria-label="Tool activity">
      <span className="bui-tool-chips-meta">
        {chips.length} step{chips.length === 1 ? "" : "s"}
      </span>
      <div className="bui-tool-chips-row">
        {chips.map((e) => (
          <span
            key={e.id || `${e.ts}:${e.label}`}
            className={`bui-chip${e.status === "fail" ? " fail" : e.status === "running" ? " run" : ""}`}
          >
            {chipLabel(e)}
          </span>
        ))}
      </div>
    </div>
  );
}
