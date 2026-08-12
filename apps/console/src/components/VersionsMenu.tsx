import { Check, ChevronDown, History } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Checkpoint } from "../api";

type Props = {
  versions: Checkpoint[];
  disabled?: boolean;
  onRestore: (checkpointId: string) => void;
};

function relativeWhen(iso?: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const mins = Math.max(0, Math.round((Date.now() - t) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 48) return `${hrs}h ago`;
  return `${Math.round(hrs / 24)}d ago`;
}

/** Restore-only version picker — no fork / build-on. */
export function VersionsMenu({ versions, disabled, onRestore }: Props) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!versions.length) return null;

  return (
    <div className="versions-menu" ref={rootRef}>
      <button
        type="button"
        className="composer-action"
        disabled={disabled}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((v) => !v)}
        title="Restore an earlier preview"
      >
        <History size={14} strokeWidth={1.5} />
        Versions
        <ChevronDown size={12} strokeWidth={1.5} />
      </button>
      {open && (
        <ul className="versions-dropdown" role="listbox" aria-label="Versions">
          {versions.map((v) => (
            <li key={v.id}>
              <button
                type="button"
                role="option"
                aria-selected={Boolean(v.current)}
                className={`versions-item${v.current ? " current" : ""}`}
                disabled={disabled || v.current}
                onClick={() => {
                  setOpen(false);
                  if (!v.current) onRestore(v.id);
                }}
              >
                <span className="versions-item-label">{v.label}</span>
                <span className="versions-item-meta">
                  {v.current ? (
                    <>
                      <Check size={12} strokeWidth={2} />
                      Current
                    </>
                  ) : (
                    relativeWhen(v.created_at)
                  )}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
