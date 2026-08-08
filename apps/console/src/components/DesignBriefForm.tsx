import { useEffect, useState } from "react";
import type { DesignBrief } from "../api";

type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";

type Props = {
  value: DesignBrief;
  onSave: (v: DesignBrief) => Promise<void>;
  disabled?: boolean;
};

export function DesignBriefForm({ value, onSave, disabled }: Props) {
  const [draft, setDraft] = useState<DesignBrief>(value);
  const [status, setStatus] = useState<SaveState>("idle");

  useEffect(() => {
    setDraft(value);
    setStatus("idle");
  }, [value]);

  const aesthetic = draft.aesthetic ?? {};

  function patchAesthetic(patch: Record<string, string>) {
    setDraft({
      ...draft,
      aesthetic: { ...aesthetic, ...patch },
    });
    setStatus("dirty");
  }

  function patchNotes(notes: string) {
    setDraft({ ...draft, user_notes: notes });
    setStatus("dirty");
  }

  async function handleSave() {
    if (disabled) return;
    setStatus("saving");
    try {
      await onSave(draft);
      setStatus("saved");
      window.setTimeout(() => setStatus((s) => (s === "saved" ? "idle" : s)), 2200);
    } catch {
      setStatus("error");
    }
  }

  const canSave = status === "dirty" || status === "error";

  return (
    <details className="design-brief-panel" open>
      <summary>
        <span>Look &amp; feel</span>
        {status === "saved" && <span className="design-brief-pill ok">Saved</span>}
        {status === "dirty" && <span className="design-brief-pill warn">Unsaved</span>}
      </summary>
      <p className="design-brief-hint">
        Steers the app Prime builds. Edit freely, then save so it is captured on the project.
      </p>
      <div className="design-brief-grid">
        <label>
          Direction
          <select
            disabled={disabled || status === "saving"}
            value={aesthetic.direction ?? "dense-ops"}
            onChange={(e) => patchAesthetic({ direction: e.target.value })}
          >
            <option value="dense-ops">Dense ops</option>
            <option value="utilitarian">Utilitarian</option>
            <option value="editorial">Editorial</option>
            <option value="soft-minimal">Soft minimal</option>
            <option value="branded-custom">Branded custom</option>
          </select>
        </label>
        <label>
          Density
          <select
            disabled={disabled || status === "saving"}
            value={aesthetic.density ?? "compact"}
            onChange={(e) => patchAesthetic({ density: e.target.value })}
          >
            <option value="comfortable">Comfortable</option>
            <option value="compact">Compact</option>
            <option value="dense">Dense</option>
          </select>
        </label>
        <label>
          Color
          <select
            disabled={disabled || status === "saving"}
            value={aesthetic.color_mode ?? "dark"}
            onChange={(e) => patchAesthetic({ color_mode: e.target.value })}
          >
            <option value="dark">Dark</option>
            <option value="light">Light</option>
            <option value="system">System</option>
          </select>
        </label>
        <label>
          Chrome
          <select
            disabled={disabled || status === "saving"}
            value={aesthetic.chrome ?? "no-cards"}
            onChange={(e) => patchAesthetic({ chrome: e.target.value })}
          >
            <option value="no-cards">No cards</option>
            <option value="cards-ok-for-interaction-only">Cards for interaction only</option>
          </select>
        </label>
        <label>
          Accent
          <input
            type="color"
            disabled={disabled || status === "saving"}
            value={aesthetic.palette?.accent ?? "#3D8B6E"}
            onChange={(e) => {
              setDraft({
                ...draft,
                aesthetic: {
                  ...aesthetic,
                  palette: { ...(aesthetic.palette ?? {}), accent: e.target.value },
                },
              });
              setStatus("dirty");
            }}
          />
        </label>
        <label className="span-2">
          Notes for Prime
          <input
            type="text"
            disabled={disabled || status === "saving"}
            value={draft.user_notes ?? ""}
            placeholder="e.g. playful, card flips, no dense tables"
            onChange={(e) => patchNotes(e.target.value)}
          />
        </label>
      </div>
      <div className="design-brief-actions">
        <button
          type="button"
          className="design-brief-save"
          disabled={disabled || !canSave}
          onClick={() => void handleSave()}
        >
          {status === "saving" ? "Saving…" : status === "saved" ? "Saved" : "Save look & feel"}
        </button>
        {status === "error" && <span className="design-brief-error">Couldn’t save — try again</span>}
        {status === "saved" && <span className="design-brief-saved">Captured on this project</span>}
      </div>
    </details>
  );
}
