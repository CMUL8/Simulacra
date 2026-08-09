import { useEffect, useRef, useState } from "react";
import type { DesignBrief } from "../api";

type Props = {
  value: DesignBrief;
  onSave: (v: DesignBrief) => Promise<void>;
  disabled?: boolean;
  /** Single-line chrome: chips + accent only (no notes field). */
  compact?: boolean;
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
      palette: {
        background: "#F4F1EC",
        surface: "#FFFcf8",
        text: "#1C1917",
        accent: "#3D8B6E",
        danger: "#B91C1C",
      },
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
      palette: {
        background: "#0B0F0E",
        surface: "#141A18",
        text: "#E8EEE9",
        accent: "#3D8B6E",
        danger: "#C44B4B",
      },
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
      palette: {
        background: "#F7F6F2",
        surface: "#FFFFFF",
        text: "#111111",
        accent: "#1A1A1A",
        danger: "#8B1E1E",
      },
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
      palette: {
        background: "#FFF8F4",
        surface: "#FFFFFF",
        text: "#1A1210",
        accent: "#FF6B4A",
        danger: "#C44B4B",
      },
    },
  },
];

function matchPreset(brief: DesignBrief): string | null {
  const dir = brief.aesthetic?.direction;
  const found = PRESETS.find((p) => p.aesthetic.direction === dir);
  return found?.id ?? null;
}

export function DesignBriefForm({ value, onSave, disabled, compact }: Props) {
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

  const chips = (
    <>
      <div className="style-segment" role="group" aria-label="Look and feel">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            className={`style-seg ${active === p.id ? "on" : ""}`}
            disabled={disabled || status === "saving"}
            onClick={() => applyPreset(p)}
          >
            {p.label}
          </button>
        ))}
      </div>
      <label className="style-accent" title="Accent color">
        <span className="style-accent-swatch" style={{ background: accent }} aria-hidden />
        <input
          type="color"
          disabled={disabled}
          value={accent}
          aria-label="Accent color"
          onChange={(e) => {
            const v = e.target.value;
            setAccent(v);
            scheduleNotesSave(notes, v);
          }}
        />
      </label>
      {status === "error" && (
        <span className="style-status err" title={err}>
          Retry
        </span>
      )}
    </>
  );

  if (compact) {
    return <div className="style-bar style-bar-compact">{chips}</div>;
  }

  return (
    <div className="style-bar">
      <div className="style-bar-row">
        <span className="style-bar-label">Style</span>
        {chips}
      </div>
    </div>
  );
}
