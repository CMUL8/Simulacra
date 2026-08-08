import { useEffect, useRef, useState } from "react";
import type { DesignBrief } from "../api";

type Props = {
  value: DesignBrief;
  onSave: (v: DesignBrief) => Promise<void>;
  disabled?: boolean;
};

type Preset = {
  id: string;
  label: string;
  aesthetic: NonNullable<DesignBrief["aesthetic"]>;
};

const PRESETS: Preset[] = [
  {
    id: "soft",
    label: "Soft",
    aesthetic: {
      direction: "soft-minimal",
      density: "comfortable",
      color_mode: "light",
      chrome: "cards-ok-for-interaction-only",
      shape: "rounded",
      motion: "subtle",
      palette: { accent: "#3D8B6E" },
    },
  },
  {
    id: "dense",
    label: "Dense",
    aesthetic: {
      direction: "dense-ops",
      density: "dense",
      color_mode: "dark",
      chrome: "no-cards",
      shape: "sharp",
      motion: "subtle",
      palette: { accent: "#3D8B6E" },
    },
  },
  {
    id: "editorial",
    label: "Editorial",
    aesthetic: {
      direction: "editorial",
      density: "comfortable",
      color_mode: "light",
      chrome: "no-cards",
      shape: "sharp",
      motion: "subtle",
      palette: { accent: "#1a1a1a" },
    },
  },
  {
    id: "playful",
    label: "Playful",
    aesthetic: {
      direction: "branded-custom",
      density: "comfortable",
      color_mode: "light",
      chrome: "cards-ok-for-interaction-only",
      shape: "rounded",
      motion: "playful",
      palette: { accent: "#FF6B4A" },
    },
  },
];

function matchPreset(brief: DesignBrief): string | null {
  const dir = brief.aesthetic?.direction;
  const found = PRESETS.find((p) => p.aesthetic.direction === dir);
  return found?.id ?? null;
}

export function DesignBriefForm({ value, onSave, disabled }: Props) {
  const [notes, setNotes] = useState(value.user_notes ?? "");
  const [accent, setAccent] = useState(value.aesthetic?.palette?.accent ?? "#3D8B6E");
  const [active, setActive] = useState<string | null>(matchPreset(value));
  const [status, setStatus] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const [err, setErr] = useState("");
  const timer = useRef<number | null>(null);

  useEffect(() => {
    setNotes(value.user_notes ?? "");
    setAccent(value.aesthetic?.palette?.accent ?? "#3D8B6E");
    setActive(matchPreset(value));
  }, [value]);

  async function persist(next: DesignBrief) {
    setStatus("saving");
    setErr("");
    try {
      await onSave(next);
      setStatus("saved");
      window.setTimeout(() => setStatus((s) => (s === "saved" ? "idle" : s)), 1600);
    } catch (e) {
      setStatus("error");
      setErr(e instanceof Error ? e.message.slice(0, 120) : "Save failed");
    }
  }

  function applyPreset(preset: Preset) {
    if (disabled) return;
    setActive(preset.id);
    const next: DesignBrief = {
      ...value,
      aesthetic: {
        ...(value.aesthetic ?? {}),
        ...preset.aesthetic,
        palette: {
          ...(value.aesthetic?.palette ?? {}),
          ...(preset.aesthetic.palette ?? {}),
          accent,
        },
      },
      user_notes: notes,
    };
    void persist(next);
  }

  function scheduleNotesSave(nextNotes: string, nextAccent: string) {
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const preset = PRESETS.find((p) => p.id === active);
      const next: DesignBrief = {
        ...value,
        aesthetic: {
          ...(value.aesthetic ?? {}),
          ...(preset?.aesthetic ?? {}),
          palette: {
            ...(value.aesthetic?.palette ?? {}),
            ...(preset?.aesthetic.palette ?? {}),
            accent: nextAccent,
          },
        },
        user_notes: nextNotes,
      };
      void persist(next);
    }, 500);
  }

  return (
    <div className="style-bar">
      <div className="style-bar-row">
        <span className="style-bar-label">Style</span>
        <div className="style-chips" role="group" aria-label="Look and feel">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              className={`style-chip ${active === p.id ? "on" : ""}`}
              disabled={disabled || status === "saving"}
              onClick={() => applyPreset(p)}
            >
              {p.label}
            </button>
          ))}
        </div>
        <label className="style-accent" title="Accent">
          <input
            type="color"
            disabled={disabled}
            value={accent}
            onChange={(e) => {
              const v = e.target.value;
              setAccent(v);
              scheduleNotesSave(notes, v);
            }}
          />
        </label>
        {status === "saved" && <span className="style-status ok">Saved</span>}
        {status === "saving" && <span className="style-status">Saving</span>}
        {status === "error" && (
          <span className="style-status err" title={err}>
            Retry
          </span>
        )}
      </div>
      <input
        className="style-notes"
        type="text"
        disabled={disabled}
        value={notes}
        placeholder="Optional notes for Prime…"
        onChange={(e) => {
          const v = e.target.value;
          setNotes(v);
          scheduleNotesSave(v, accent);
        }}
      />
    </div>
  );
}
