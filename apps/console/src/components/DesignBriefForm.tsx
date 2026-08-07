import type { DesignBrief } from "../api";

type Props = {
  value: DesignBrief;
  onChange: (v: DesignBrief) => void;
  disabled?: boolean;
};

export function DesignBriefForm({ value, onChange, disabled }: Props) {
  const aesthetic = value.aesthetic ?? {};

  function patchAesthetic(patch: Record<string, string>) {
    onChange({
      ...value,
      aesthetic: { ...aesthetic, ...patch },
    });
  }

  return (
    <details className="design-brief-panel">
      <summary>Look &amp; feel</summary>
      <div className="design-brief-grid">
        <label>
          Direction
          <select
            disabled={disabled}
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
            disabled={disabled}
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
            disabled={disabled}
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
            disabled={disabled}
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
            disabled={disabled}
            value={aesthetic.palette?.accent ?? "#3D8B6E"}
            onChange={(e) =>
              onChange({
                ...value,
                aesthetic: {
                  ...aesthetic,
                  palette: { ...(aesthetic.palette ?? {}), accent: e.target.value },
                },
              })
            }
          />
        </label>
        <label className="span-2">
          Notes for Prime
          <input
            type="text"
            disabled={disabled}
            value={value.user_notes ?? ""}
            placeholder="e.g. sharp edges, no pills, accent only on CTAs"
            onChange={(e) => onChange({ ...value, user_notes: e.target.value })}
          />
        </label>
      </div>
    </details>
  );
}
