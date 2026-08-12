import { useEffect, useMemo, useRef, useState } from "react";

export type PaletteItem = {
  id: string;
  label: string;
  hint?: string;
  group: string;
  disabled?: boolean;
  onSelect: () => void;
};

type Props = {
  open: boolean;
  items: PaletteItem[];
  onClose: () => void;
};

export function CommandPalette({ open, items, onClose }: Props) {
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  const filtered = useMemo(() => {
    const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) return items.slice(0, 24);
    return items
      .filter((item) => {
        const hay = `${item.label} ${item.hint || ""} ${item.group}`.toLowerCase();
        return words.every((w) => hay.includes(w));
      })
      .slice(0, 24);
  }, [items, query]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setIndex(0);
    const t = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(t);
  }, [open]);

  useEffect(() => {
    setIndex(0);
  }, [query]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setIndex((i) => Math.min(filtered.length - 1, i + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const hit = filtered[index];
        if (hit && !hit.disabled) {
          hit.onSelect();
          onClose();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, filtered, index, onClose]);

  if (!open) return null;

  const groups = filtered.reduce<Record<string, PaletteItem[]>>((acc, item) => {
    (acc[item.group] ||= []).push(item);
    return acc;
  }, {});

  let running = -1;

  return (
    <div className="palette-scrim" onMouseDown={onClose} role="presentation">
      <div
        className="palette"
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <input
          ref={inputRef}
          className="palette-input"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Go to…"
          aria-label="Filter commands"
        />
        <ul className="palette-list" role="listbox">
          {filtered.length === 0 && <li className="palette-empty">No matches</li>}
          {Object.entries(groups).map(([group, rows]) => (
            <li key={group} className="palette-group">
              <span className="palette-group-label">{group}</span>
              <ul>
                {rows.map((item) => {
                  running += 1;
                  const i = running;
                  return (
                    <li key={item.id}>
                      <button
                        type="button"
                        role="option"
                        aria-selected={i === index}
                        className={`palette-item${i === index ? " on" : ""}${item.disabled ? " disabled" : ""}`}
                        disabled={item.disabled}
                        onMouseEnter={() => setIndex(i)}
                        onClick={() => {
                          if (item.disabled) return;
                          item.onSelect();
                          onClose();
                        }}
                      >
                        <span className="palette-item-label">{item.label}</span>
                        {item.hint ? <span className="palette-item-hint">{item.hint}</span> : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
