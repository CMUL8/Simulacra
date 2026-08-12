/** Tool chips — compact unique tool/call pills (Beautiful UI 05). */
import type { AgentEvent } from "../../api";

type Props = {
  events: AgentEvent[];
  limit?: number;
};

const NOISE =
  /^(agent|working|ready|session ready|turn finished|ipython|tool|using tool|building app|reading sources)$/i;

function chipLabel(e: AgentEvent): string {
  const label = (e.label || "").trim();
  if (label) return label.length > 36 ? `${label.slice(0, 34)}…` : label;
  const tool = String(e.meta?.tool || "tool");
  return tool.replace(/_/g, " ");
}

export function ToolChips({ events, limit = 4 }: Props) {
  const chips: AgentEvent[] = [];
  const seen = new Set<string>();
  for (let i = events.length - 1; i >= 0 && chips.length < limit; i--) {
    const e = events[i]!;
    if (e.type !== "tool" && e.type !== "phase") continue;
    const label = chipLabel(e);
    if (!label || NOISE.test(label)) continue;
    const key = label.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    chips.unshift(e);
  }

  if (!chips.length) return null;

  return (
    <div className="bui-tool-chips" aria-label="Tool activity">
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
