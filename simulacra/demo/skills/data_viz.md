# Internal data-app visualization craft

Use this when customizing Simulacra apps. The goal is not a generic dashboard — it is a **memorable, legible command surface** for operators.

## Hierarchy (must)

1. **One glance truth** — top KPI strip: 3–5 metrics max, largest number first, quiet labels.
2. **Primary viz** — one hero chart that answers the user’s question (risk mix, trend, leaderboard).
3. **Evidence table** — scannable rows; risk color as a left rail or cell tint, not rainbow chrome.
4. **Detail** — side panel or row expand; never open a new page for one finding.

## Visual rules

- Prefer **position + length** (bars, ranked lists) over pie charts for comparisons.
- Encode risk with a **single hue family** (e.g. red→amber→green) on a dark or light ground — never neon purple glow.
- Dense ops: hairline borders, tight padding, mono for IDs/scores; Editorial: more whitespace, stronger display type.
- No emoji. No rounded-full pill spam. Cards only when they hold an interaction.
- Accent color from the design brief is for **focus** (selected tab, primary action, key bar) — not every border.

## Data wiring

- Read `public/analytics.json` and `public/data.json` — do not invent KPIs.
- Label axes and legends with human words from the data (`vendor`, `theme`, `risk_level`).
- Empty states: one calm sentence + what to attach — never a blank white panel.

## Impress bar

Before finishing, ask: would a risk lead screenshot this for a weekly review? If it still looks like a stock template, push further on typography, spacing, and the hero viz.
