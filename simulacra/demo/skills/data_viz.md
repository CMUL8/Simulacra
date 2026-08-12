# Internal data-app visualization craft

Use this when customizing Simulacra **data_app** artifacts. Match the **user's topic** — do not keep Vendor Risk / diligence chrome unless they asked for that or the data is clearly that shape.

## Hierarchy (must)

1. **One glance truth** — top KPI strip: 3–5 metrics max that matter for THIS topic, largest number first, quiet labels.
2. **Primary viz** — one hero chart that answers the user’s question (trend, mix, leaderboard, map — whatever fits).
3. **Evidence table** — scannable rows; status color as a left rail or cell tint when relevant, not rainbow chrome.
4. **Detail** — side panel or row expand when useful.

## Visual rules

- Prefer **position + length** (bars, ranked lists) over pie charts for comparisons.
- Encode severity (if any) with a **single hue family** — never neon purple glow.
- Dense ops: hairline borders, tight but even padding (≥14px inside panels), mono for IDs/scores.
- Editorial: more whitespace, stronger display type.
- No emoji. No rounded-full pill spam. Do not flood one KPI with a bright fill while siblings stay dark.
- Accent from the design brief is for **focus** — not every border and not a whole card background.

## Contrast & tracks (non-negotiable)

- `--text` must clearly contrast with `--bg` / `--panel`.
- `--muted` must stay readable (never near-black on black).
- `--panel-2` must **differ** from `--panel` so bar tracks and meters are visible.
- Labels and values need breathing room — never kiss panel edges.

## Data wiring

- Read `public/analytics.json` and `public/data.json` — do not invent KPIs.
- Label axes and legends with human words from the actual columns.
- Empty states: one calm sentence + what to attach — never a blank white panel.
- Discard scaffold demo copy (findings/vendors/risk score) when the room or prompt is about something else.

## Impress bar

Before finishing, ask: would someone who cares about this topic screenshot it? If labels are illegible or the IA is the wrong product domain, keep editing `src/styles.css` and `src/App.tsx`.
