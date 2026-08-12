/** Selection actions — highlight passage → hand to agent (Beautiful UI 19). */
import { useEffect, useState } from "react";

type Props = {
  rootSelector?: string;
  disabled?: boolean;
  onAction: (action: "explain" | "improve" | "shorten" | "tone", text: string) => void;
};

type Pop = { x: number; y: number; text: string };

export function SelectionActions({ rootSelector = ".cursor-answer", disabled, onAction }: Props) {
  const [pop, setPop] = useState<Pop | null>(null);

  useEffect(() => {
    if (disabled) {
      setPop(null);
      return;
    }

    function onUp(ev: MouseEvent) {
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) {
        setPop(null);
        return;
      }
      const text = sel.toString().trim();
      if (text.length < 8 || text.length > 1200) {
        setPop(null);
        return;
      }
      const node = sel.anchorNode;
      const el = node instanceof Element ? node : node?.parentElement;
      if (!el?.closest(rootSelector)) {
        setPop(null);
        return;
      }
      const range = sel.getRangeAt(0);
      const rect = range.getBoundingClientRect();
      if (!rect.width && !rect.height) {
        setPop(null);
        return;
      }
      setPop({
        x: Math.min(window.innerWidth - 200, Math.max(12, rect.left + rect.width / 2 - 90)),
        y: Math.max(8, rect.top - 44),
        text,
      });
    }

    function onScroll() {
      setPop(null);
    }

    document.addEventListener("mouseup", onUp);
    document.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("mouseup", onUp);
      document.removeEventListener("scroll", onScroll, true);
    };
  }, [disabled, rootSelector]);

  if (!pop) return null;

  const actions: { id: "explain" | "improve" | "shorten" | "tone"; label: string }[] = [
    { id: "explain", label: "Explain" },
    { id: "improve", label: "Improve" },
    { id: "shorten", label: "Shorten" },
    { id: "tone", label: "Tone" },
  ];

  return (
    <div className="bui-sel-actions" style={{ left: pop.x, top: pop.y }} role="toolbar" aria-label="Selection actions">
      {actions.map((a) => (
        <button
          key={a.id}
          type="button"
          onClick={() => {
            onAction(a.id, pop.text);
            setPop(null);
            window.getSelection()?.removeAllRanges();
          }}
        >
          {a.label}
        </button>
      ))}
    </div>
  );
}

export function selectionPrompt(action: "explain" | "improve" | "shorten" | "tone", text: string): string {
  const clip = text.length > 400 ? `${text.slice(0, 397)}…` : text;
  if (action === "explain") return `Explain this passage briefly:\n\n"${clip}"`;
  if (action === "improve") return `Improve this passage for clarity and craft:\n\n"${clip}"`;
  if (action === "shorten") return `Shorten this passage without losing the point:\n\n"${clip}"`;
  return `Rewrite this passage with a sharper, more confident tone:\n\n"${clip}"`;
}
