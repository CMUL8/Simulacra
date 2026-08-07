# Simulacra — Product & Technical Specification

**Status:** Draft v0.3  
**Date:** 2026-08-07  
**Runtime:** [Prime Agent](https://github.com/PrimeIntellect-ai/prime-agent) (unforked, used in pure form)  
**Related:** [Roadmap](./ROADMAP.md)

---

## 1. Brand

| | |
| --- | --- |
| **Name** | Simulacra |
| **Category** | Governed vibe coding for internal data apps |
| **Tagline** | Natural language to production analytics apps — with persistent execution, eval gates, and audit trails. |
| **One-liner** | Turn research folders, tables, and warehouses into governed, deployable internal data apps. |
| **Promise** | Explore → structure → verify → ship, without giving the agent the keys to production. |
| **Voice** | Precise, technical, non-hype. Governance is the product; “vibe” is the UX. |

### Positioning

> **Simulacra** is governed vibe coding for internal data apps — natural language to production analytics apps on your warehouse, with persistent execution, eval gates, and audit trails.

**Near-term (honest) positioning for v0–v1:**

> Shared research on governed data rooms (folders, files, tables) → reproducible structured outputs and internal apps, with eval gates and an audit trail. Warehouse connectors and one-click deploy come next on the same loop.

### What we are / are not

| We are | We are not |
| --- | --- |
| A **control plane** over Prime Agent for data work | A fork of Prime Agent |
| A **governed run + artifact** product | A generic chat-with-docs toy |
| An **internal data app** builder | A consumer BI tool (Tableau replacement) |
| Audit-first for enterprises | A multi-tenant public sandbox SaaS on day one |

---

## 2. Problem

Enterprises have:

1. **Unstructured piles** — research shares, diligence rooms, PDF/HTML/log dumps, CSV mishmash.  
2. **Tabular / semi-structured** extracts that never become durable products.  
3. **Warehouses** with certified metrics that analysts still glue into one-off Streamlit apps.  
4. **No closed loop** from NL task → verified artifact → deployable internal app with audit.

Today’s agents either (a) chat and forget, or (b) code freely with no data governance. Simulacra closes the loop with **Prime as the execution engine** and **Simulacra as policy, contract, gates, and ship**.

---

## 3. Ideal end state (north star)

A user (or system) says:

> “From this data room, build an internal app that tracks vendor risk scores weekly and deploys behind SSO.”

Simulacra:

1. Mounts an allowed **source** (folder / table / warehouse view).  
2. Runs a **Prime Agent** session (persistent IPython, optional `rlm` children).  
3. Emits a **run contract** (`manifest.json` + artifacts).  
4. Passes **eval gates** (schema, tests, policy).  
5. Produces a **deployable app** (template-bound).  
6. On approval, **deploys** to an internal target and retains an **audit pack**.

---

## 3A. Prompt → app → iterate (canonical UX loop)

This is the product. Everything else (CLI, connectors, deploy) exists to make this loop feel inevitable.

```text
Landing          Plan (read-only)         Approve          Build                 Ready → Iterate
─────────        ────────────────         ───────          ─────                 ───────────────
empty prompt  →  scan + explore chat  →  human gate  →  extract→gates→app  →  preview + refine
@ files          no writes to app/        intentional      Prime does hard work   Prime re-enters
```

### 3A.1 Experience principles

| Principle | Meaning |
| --- | --- |
| **Chat is the IDE** | Centered conversation; preview opens on demand — not a busy dashboard on first paint. |
| **Plan before commit** | Nothing mutable (parquet rewrite, app scaffold, preview process) until **Approve & Build**. |
| **Simulacra owns the rails** | Sources, extract contract, gates, audit, deploy keys, template jail. Deterministic where possible. |
| **Prime owns the hard stuff** | Understanding messy intent, exploring data in context, writing/editing app code, multi-step refinement. |
| **One continuous session** | Same Prime session across plan → build → iterate whenever possible (compaction OK; cold restart is a failure mode). |
| **Show the work** | Stream Prime tool/think events into the UI (SSE). Silence feels broken; traces feel like an agent. |
| **Never fake competence** | If Prime is off or fails, say so and fall back honestly — do not narrate changes that did not happen. |
| **Iterate = rebuild delta** | Follow-ups must change artifacts (code/config/data views), not only chat text. |
| **Stay interactive** | Builds never lock the console; Stop works; one builder job per project. |
| **Taste travels as data** | Aesthetics and IA choices live in `design_brief` and are passed into every Prime build. |

### 3A.2 Phase-by-phase behavior

#### Phase 0 — Landing

**User:** types a goal; optional `@file` tags from the data room.  
**Simulacra:** creates `run_<id>/`, copies/mounts allowed inputs, sets `phase=plan`.  
**Prime:** not called yet.  
**UI:** one composition — brand, prompt, CTA. No fake activity.

#### Phase 1 — Plan (read-only)

**Goal:** cheap, reversible understanding before any build cost.

| Step | Owner | What happens |
| --- | --- | --- |
| Scan | **Simulacra** | Deterministic extract preview (file list, sample rows, counts). Fast; reproducible. |
| Opening reply | **Simulacra** | Structured plan summary (scope, proposed app title, “approve when ready”). |
| Plan chat | **Prime** (`ask` or light `run`) | Answer questions, refine requirements, interpret `@` tags, propose schema/UI shape. **Read-only cwd.** |
| Prompt merge | **Simulacra** | Append clarifying messages into the canonical task string used at build. |

**Prime must not:** write parquet, sync templates, start preview, claim “built.”  
**Prime should:** use tools/IPython against `inputs/` + plan preview JSON when the question needs evidence (“which vendors are high risk?”), not invent counts.

**Exit:** user clicks **Approve & Build** (explicit human gate).

#### Phase 2 — Build (Approve & Build)

**Goal:** one gated pipeline that ends in a live preview. Leave creativity and coding to Prime; leave policy and plumbing to Simulacra.

| Step | Owner | Prime mode | Notes |
| --- | --- | --- | --- |
| 1. Re-extract → parquet + summary | **Simulacra** | — | Deterministic extractors preferred; LLM extract only when code extract fails. |
| 2. Eval gates | **Simulacra** | — | Fail closed; no app if gates fail. |
| 3. Sync base template → `app/` | **Simulacra** | — | Always re-sync so runs are not stale. Jail: Prime stays inside `app/`. |
| 4. **App build** | **Prime** | `run` (tools + edits) | **Primary Prime call.** Bounded (§3A.7). Obey `design_brief`. Customize `src/`, tokens, KPIs, layout. |
| 5. Optional config polish | **Prime** | fold into step 4 | Avoid second cold start. |
| 6. Preview boot + smoke | **Simulacra** | — | `app_boots` gate; do not ask Prime to manage processes. |
| 7. Checkpoint | **Simulacra** | — | Snapshot for rollback. |

**Build UX:** status via live events (“Reading data room” → “Gates” → “Prime building app” → “Preview ready”), then a single assistant message with title + row count + “Open Preview.”

#### Phase 3 — Ready / Iterate

**Goal:** Cursor-like refine loop. Every useful message ends in a visible product change.

| User intent | Simulacra | Prime |
| --- | --- | --- |
| Copy / layout / new view / filter | Checkpoint → open same session cwd=`app/` | **`run`** edit React/CSS/data wiring |
| “What does the data say?” | Provide parquet/DuckDB context | **`ask`/`run`** analytical reply; no deploy |
| Re-scope extract (“also pull X”) | Re-run extract + gates | Then **`run`** rebuild app against new data |
| Rollback | Restore checkpoint + restart preview | Not called |

**Anti-pattern (current demo debt):** follow-up that only chats and restarts the same template. Iterations must invoke the **builder** path (or a scoped delta builder), not `ask`-only narration.

#### Phase 4 — Deploy (later)

Human approve → release record (`run_id`, hashes, approver) → internal URL. Prime is **not** in the deploy hot path; it already finished when gates were green.

### 3A.3 When Prime is called (contract)

| Moment | Call | Session | Timeout (guide) | Success = |
| --- | --- | --- | --- | --- |
| Plan chat turn | `ask` or short `run` | Prefer **persistent** plan session per project | 60–120s | Useful markdown; optional tool evidence |
| Approve & Build | **`run`** (primary) | **New or continued** build session; cwd = `app/` (+ read `outputs/`) | 180–300s | File diffs in `app/src`; build still compiles |
| Post-build polish | `ask` JSON only if needed | Same build session | 60s | Valid config merge |
| Iterate (UI change) | **`run`** | **Same** build session if alive | 120–240s | Diff + preview refresh |
| Iterate (question only) | `ask` | Same session | 60–120s | Answer; no spurious rebuild |

**Flags:** `SIMULACRA_USE_PRIME=1` enables the above. When off, Simulacra heuristics/templates must still complete the loop — labeled as heuristic in UI and `manifest.prime.source`.

### 3A.4 Division of labor — “leave the hard stuff to Prime”

| Hard / fuzzy (→ **Prime**) | Easy / dangerous / must-be-true (→ **Simulacra**) |
| --- | --- |
| Interpreting vague NL goals | Mounting allowlisted sources |
| Deciding which UI surfaces matter | Writing parquet with stable schema |
| Editing React/TS/CSS to match intent | Gate catalog + pass/fail |
| Multi-step tool use, recovery from edit mistakes | Network/secret policy; deploy keys |
| Explaining tradeoffs in plan mode | Audit pack, hashes, checkpoints |
| Creative copy, KPI framing, layout taste | Template sync, preview process lifecycle |
| Applying `design_brief` to CSS/IA | Validating brief schema + defaults; Stop/timeout |

**Rule of thumb:** if the work needs judgment, code synthesis, or multi-file edits → Prime `run`. If the work must be identical tomorrow with the same inputs → Simulacra code.

### 3A.5 Making the most of Prime

1. **One session per project phase chain** — `no_session=True` is for smoke tests only. Product path persists session id in `manifest.prime` / `state`.  
2. **Fat context, thin prompts** — give Prime `outputs/summary.md`, sample rows, `config.json`, the **design brief** (§3A.10), and the approved task — not a novel. Skills teach the rails (`simulacra_*`).  
3. **`run` for builds, `ask` for talk** — do not ask Prime to “describe what you would change”; ask it to change files.  
4. **Template as floor, not ceiling** — sync a strong command-center scaffold, then let Prime customize; never ship raw template as “AI built” without a Prime pass when Prime is enabled.  
5. **Stream everything** — map Prime events → SSE → Trace panel so wait time feels like agency.  
6. **Fail soft, label hard** — on Prime timeout/error: keep last good template, emit `prime.error` in manifest, tell the user in chat.  
7. **Budget visibility** — surface model + step count; map to Prime autonomy/budget knobs.  
8. **Jail the cwd** — builder may only write under `app/` (and read `outputs/`, `inputs/` as needed). Simulacra enforces; skills reinforce.  
9. **Hard stop beats soft hope** — every Prime invocation has wall-clock timeout, max tool steps, and a cancel path (§3A.9).  
10. **Design is data** — aesthetics and product choices are first-class structured inputs, not vibes buried in chat (§3A.10).

### 3A.6 How to run Prime Agent properly

Simulacra never “hopes” Prime finishes. Every product call is an **orchestrated job** with explicit start, bounds, observe, stop, and settle.

#### Lifecycle (per invocation)

```text
prepare context → spawn/attach session → send task → stream events
        → idle OR timeout OR cancel OR step-budget → settle artifacts → reply to UI
```

| Stage | Simulacra must |
| --- | --- |
| **Prepare** | Write `work/prime_task.md` (goal + design brief + constraints + success criteria). Point cwd correctly (`app/` for build/iterate; project root read-only for plan). |
| **Attach** | Reuse `state.prime.session_id` if alive; else start once. Prefer daemon/RPC over one-shot process churn. |
| **Send** | One clear task. Prefer `Agent.run(...)` for builds/iterates; `ask` only for Q&A. |
| **Observe** | Forward events to SSE. Keep UI interactive (§3A.9). |
| **Stop** | On idle (done), timeout, cancel, or max steps — call stop / kill cleanly; never leave orphan uvicorn/node/Prime children. |
| **Settle** | Diff `app/`; typecheck/build smoke if cheap; update manifest `prime.{session_id,model,steps,duration_ms,status}`; checkpoint on success. |

#### `ask` vs `run` (product rule)

| Use | Mode | Why |
| --- | --- | --- |
| Plan Q&A, “what does the data say?”, clarifying questions | `ask` (or short `run` with tools if evidence needed) | Fast; no expectation of file edits |
| Approve & Build, UI/layout/copy changes, “make it darker / denser / cardless” | **`run`** | Must produce file diffs; narration without diffs is a failed run |
| Config-only polish after a successful `run` | Avoid a second cold start — fold into the same `run` task | Two sessions = double latency and loop risk |

#### Proper run options (defaults for product path)

| Knob | Plan chat | Build | Iterate (UI) | Iterate (Q&A) |
| --- | --- | --- | --- | --- |
| Wall timeout | 90s | 240s (hard cap 300s) | 180s | 90s |
| Max tool/steps | 8 | 40 | 25 | 6 |
| Collect events | yes | yes | yes | optional |
| Network from Prime | deny / allowlist | deny (app is local) | deny | deny |
| Write roots | none (or `work/` only) | `app/` | `app/` | none |
| Success check | non-empty reply | ≥1 file change under `app/src` **or** explicit “no change needed” + reason | same | reply only |
| On failure | heuristic plan reply | keep template + label | keep last checkpoint | say could not answer |

`no_session=True` is **dev/smoke only**. Product path: persistent session, compact when context is huge, restart only if RPC is dead.

#### Task shape Prime should receive

Every build/iterate `run` prompt includes, in order:

1. **Role** — “You are Simulacra’s builder; edit files; do not lecture.”  
2. **User goal** — approved task string (merged plan chat).  
3. **Design brief** — structured aesthetics / product choices (§3A.10).  
4. **Data facts** — row counts, schema, paths to `public/*.json`.  
5. **Constraints** — cwd jail, no new deps unless allowlisted, keep TypeScript valid, integration control layer owns data access.  
6. **Done when** — concrete exit: “Preview-worthy UI matching design brief; stop when compile-safe and brief is satisfied.”  
7. **Anti-loop** — “Do not re-read the same files more than twice; do not restart the app process; Simulacra owns preview.”

#### What Simulacra must never ask Prime to do

- Start/stop preview servers, install global packages, open network to prod systems  
- Bypass gates or write outside `app/` / approved `outputs/`  
- “Keep improving until perfect” / unbounded refine  
- Re-extract the whole data room when a UI tweak was requested  

### 3A.7 Interactivity & anti-loop (console never freezes)

The console must stay **interactive** while Prime works. Endless agent loops are a product bug, not a model personality quirk.

#### UI contract while Prime is running

| Rule | Behavior |
| --- | --- |
| **Non-blocking API** | Approve/build and follow-ups return quickly with `status=running` **or** stream via SSE; HTTP handlers must not hold the only event-loop worker for 5+ minutes without heartbeats. |
| **Always cancelable** | Visible **Stop** on every Prime job. Cancel → RPC stop → settle → chat “Stopped — last good preview kept.” |
| **One builder at a time** | Per project: queue or reject concurrent builds (“Already building — Stop or wait”). Never stack parallel `run`s on the same `app/`. |
| **Chat stays usable** | User can keep typing; messages queue as “pending after this build” or are plan-only if still in plan phase. |
| **Preview stays up** | During iterate, prefer hot edit of running preview; do not kill preview until new build settles (or swap atomically). |
| **Heartbeat** | If no Prime event for N seconds, show “still working…”; if no event for M seconds (e.g. 45s) treat as stall → soft cancel + message. |
| **Progress is real** | Trace panel shows tool names / files touched; fake timer steps are forbidden. |

#### Loop / runaway guards (Simulacra-enforced)

| Guard | Default | On trip |
| --- | --- | --- |
| Wall-clock timeout | per phase table above | Stop Prime; keep last good artifacts; chat + `prime.status=timeout` |
| Max tool steps | per phase | Same as timeout |
| Repeated tool signature | same tool+args ≥3 times | Inject stop hint once; if continues, hard stop |
| No file diff after K steps (build/iterate) | K=15 | Stop; “Prime made no durable changes” |
| Same error twice (e.g. tsc fail) | 2 | Stop with error excerpt; offer rollback |
| Session thrash | >2 process restarts / job | Fail job; do not respawn in a loop |
| User Stop | immediate | Highest priority |

#### Job state machine (per project)

```text
idle ──start──► running ──idle/success──► settling ──► idle
                  │
                  ├──timeout/budget/stall──► failed ──► idle (preview = last good)
                  └──cancel────────────────► cancelled ──► idle
```

Only `idle` (or `failed`/`cancelled` after settle) accepts a new builder `run`. Plan `ask` may run while preview is up; it must not mutate `app/`.

#### Avoiding “endless improve” product traps

- Never auto-chain “build → critique → rebuild” without a **user message**.  
- Never set Prime goal to “make the best app possible.” Set **done-when** from the design brief + user ask.  
- After success, return control to the user; suggest 1–2 next tweaks, do not start them.  
- Autopilot / multi-step goals (later) still need a step budget and a human continue gate between major phases.

### 3A.8 Design brief — communicating aesthetics & product choices to Prime

Best apps come from **clear taste inputs**, not hoping the model invents a brand. User choices must be captured early and **injected into every Prime build/iterate task**.

#### Where choices are collected

| Moment | UI | Stored as |
| --- | --- | --- |
| Landing / Plan | Optional **Look & feel** controls (collapsed by default so hero stays clean) | `state.design_brief` |
| Plan chat | NL like “make it dense and editorial, no cards, forest green” | Merged into brief via Prime or light parser |
| Ready / Iterate | “More minimal”, “darker”, “add a findings table” | Patch brief + trigger builder `run` |
| Advanced | Paste brand tokens / reference URLs (allowlisted) | `design_brief.references[]` |

Defaults exist so zero-choice users still get a coherent look; defaults are **explicit in the brief**, not hidden in template CSS only.

#### `design_brief` schema (product contract)

```json
{
  "product_name": "Vendor Risk Command Center",
  "one_liner": "Monitor vendor findings and risk scores",
  "audience": "internal risk / ops",
  "aesthetic": {
    "direction": "editorial | utilitarian | dense-ops | soft-minimal | branded-custom",
    "density": "comfortable | compact | dense",
    "color_mode": "light | dark | system",
    "palette": {
      "background": "#0B0F0E",
      "surface": "#141A18",
      "text": "#E8EEE9",
      "accent": "#3D8B6E",
      "danger": "#C44B4B"
    },
    "typography": {
      "display": "newsreader | ibm-plex-sans | ...",
      "body": "ibm-plex-sans | ..."
    },
    "shape": "sharp | soft",
    "chrome": "no-cards | cards-ok-for-interaction-only",
    "motion": "none | subtle | expressive"
  },
  "information_architecture": {
    "primary_view": "overview | findings | vendors | custom",
    "must_have": ["KPI strip", "findings table", "vendor leaderboard"],
    "must_not": ["emoji", "purple glow", "generic Inter-on-white"]
  },
  "copy_tone": "precise | plain | executive",
  "references": [],
  "user_notes": "No rounded pills; full-bleed header; accent only on CTAs"
}
```

Simulacra validates/fills defaults; Prime does not invent a conflicting aesthetic when the brief is present.

#### How the brief is fed to Prime

1. Write `app/public/design_brief.json` (and `work/design_brief.json`) on approve and on each brief patch.  
2. Prefix every builder task with: **“Obey `public/design_brief.json` over template defaults.”**  
3. Call out deltas on iterate: **“Design brief changed: density→dense, accent→#3D8B6E. Update CSS variables and chrome; keep data wiring.”**  
4. In plan mode, Prime may **propose** a brief (“I suggest utilitarian + compact for ops”); user accepts → written to state **before** build.  
5. Skills: `simulacra_app_scaffold` reads the brief when syncing tokens into CSS variables so Prime starts on-brand, then refines.

#### Aesthetic quality bar (done-when for visual work)

Prime’s build is not done until:

- CSS variables match brief palette/typography (or documented deviation).  
- Layout matches `chrome` + `density` (e.g. `no-cards` ⇒ no decorative card wrappers).  
- Forbidden patterns in `must_not` are absent.  
- Primary view and `must_have` are present and wired to real data.  
- App remains interactive (filters, tabs, detail panels as required) — not a static mock.

#### Anti-patterns for taste

- Burying “make it pretty” only in free text with no brief object.  
- Letting Prime pick purple-glow / Inter defaults when the brief specified otherwise.  
- Re-syncing the stock template **after** Prime customized, wiping design work (re-sync only as a deliberate “reset design” action).  
- Asking Prime to match a screenshot without also extracting tokens into the brief.

### 3A.9 Target experience (happy path narrative)

1. User opens console (`:5173`), states a goal, tags `@vendor_scores.csv`, optionally sets Look & feel (e.g. dense ops, dark, green accent, no cards).  
2. Plan opens instantly with scan stats; user asks “focus on high-risk vendors”; Prime answers from preview data; brief may be refined.  
3. User hits **Approve & Build**. Trace shows extract → gates → **Prime building app** (bounded steps). UI stays usable; **Stop** is available.  
4. Preview matches the design brief — bespoke command center, not a generic table.  
5. User: “Tighter density and critical-only filter.” Brief patches; Prime `run` edits `App.tsx` / CSS within budget; preview updates; checkpoint saved.  
6. If Prime stalls, Simulacra stops it, keeps last good preview, and says so. User can retry or rollback.

### 3A.10 Honest gap vs demo today (track to close)

| Area | Today (demo) | Target (this section) |
| --- | --- | --- |
| Plan chat | Prime `ask` or heuristic; little tool use | Evidence-backed plan session |
| Build | Template sync + optional Prime `run`; separate config `ask` | Single bounded build `run` + design brief |
| Iterate | Often chat + heuristic config + restart preview | Prime `run` delta; preview stays up; queue/cancel |
| Sessions | `no_session=True` | Persistent per project; clean stop |
| Run bounds | Timeout only | Timeout + max steps + stall + repeat-tool guards |
| UI during build | Can feel stuck / connection dies | Non-blocking + SSE + Stop + one-job-per-project |
| Design/aesthetics | Implicit in free-text prompt | First-class `design_brief` in every Prime task |
| Extract | Heuristic only | Heuristic first; Prime code-gen extract when needed |
| UX honesty | Can over-claim | Manifest + chat always label Prime vs heuristic |

---

## 4. Principles

1. **Prime stays pure** — no fork; configure, skill-pack, wrap, brand.  
2. **Authority outside the model** — policy, allowlists, gates, deploy keys live in Simulacra.  
3. **Artifacts over chat** — every successful run leaves files + manifest + audit.  
4. **Progressive data plane** — unstructured → tabular → warehouse → apps (same loop).  
5. **Read by default, promote by gate** — writes to `outputs/` only unless explicitly staged.  
6. **Thin then thick** — v0 proves research→table; later proves warehouse→app.  
7. **One app template until it hurts** — avoid framework sprawl.  
8. **Prime does the hard work** — Simulacra scaffolds and governs; Prime explores, codes, and iterates (see §3A).  
9. **Approve is sacred** — plan mode never mutates the shippable app; build starts only on explicit approval.  
10. **Bounded agency** — every Prime job has timeout, step budget, cancel, and settle; no unbounded auto-refine.  
11. **Taste is structured** — user aesthetics and product choices travel as `design_brief`, not hope.  
12. **UI stays interactive** — agent work is backgrounded and observable; the console never deadlocks on a single RPC.

---

## 5. Users & jobs

| Persona | Job-to-be-done | Success |
| --- | --- | --- |
| Analytics / data engineer | Turn messy inputs into durable tables + small apps | Gate-green run + deploy |
| Research / ops lead | Share a folder + question; get a table/report back | Reproducible `outputs/` + audit |
| Platform / IT | Allow agentic data work without prod blast radius | Policy enforced; no ambient creds |
| Domain owner (light) | Ask in NL; approve ship | App URL + change record |

---

## 6. Product surfaces

| Surface | Role | Phase |
| --- | --- | --- |
| **CLI** `simulacra` | Create run, attach policy, show gates, export audit | v0 |
| **Run workspace** | Layout: `inputs/`, `outputs/`, `app/`, `audit/` | v0 |
| **Prime TUI** | Pure Prime under Simulacra defaults (branded launcher) | v0 |
| **Web console** (optional) | Run list, approve deploy, audit browser | v1+ |
| **Connectors** | Folder (built-in), warehouse adapters | v0 folder; v1+ WH |
| **App runtime** | Deploy target for generated apps | v1–v2 |

Headless: Prime `--mode rpc` / `--mode json` / ACP as needed. Prefer **packaging Prime**, not reimplementing a second agent.

---

## 7. Data plane (progressive)

```text
Phase A          Phase B           Phase C              Phase D
Unstructured  →  Tabular store  →  Warehouse connect  →  Deployable apps
(folder/room)    (parquet/duck)    (SF/BQ/DBX views)     (internal app)
```

### 7.1 Phase A — Unstructured data rooms

**Input:** governed folder (PDF, HTML, Markdown, TXT, images, CSV/JSON mixed).  
**Task:** NL research / extraction / classification.  
**Output:** structured tables + narrative + manifest.

**Controls:**
- Allowed roots only  
- Network deny-by-default (or allowlist)  
- Write only under run `outputs/`  
- Optional PII redaction hooks on export  

### 7.2 Phase B — Tabular / local analytics

**Input:** Phase A outputs and/or uploaded tables.  
**Engine:** DuckDB / Parquet in the run workspace (fits Prime’s IPython).  
**Output:** cleaned tables, metrics stubs, lightweight exploratory app scaffold.

### 7.3 Phase C — Warehouse connections

**Input:** read-only views / certified datasets (Snowflake, BigQuery, or Databricks — pick one first).  
**Controls:**
- Short-lived tokens injected by Simulacra (not stored in harness text)  
- SQL allowlist / read-only role  
- Row/column policy delegated to WH where possible  
- Query logging into audit pack  

**Output:** same manifest + optional materialized extracts into `outputs/` for reproducibility.

### 7.4 Phase D — Deployable internal apps

**Input:** gate-green run with `app/` scaffold.  
**Templates (start with one):** Streamlit *or* lightweight FastAPI+HTMX (decide in roadmap).  
**Deploy:** internal URL, SSO later, secrets from platform vault.  
**Lifecycle:** versioned release tied to `run_id` + git commit of generated app.

---

## 8. Canonical run contract

Every Simulacra run is a directory:

```text
run_<id>/
  simulacra.yaml          # policy + task + source bindings
  inputs/                 # mounted or copied sources (read-mostly)
  work/                   # agent scratch (ephemeral OK)
  outputs/
    table.parquet         # primary structured result (or splits)
    summary.md
    manifest.json         # REQUIRED
  app/                    # optional until Phase D
  audit/
    session.jsonl         # or pointer to Prime session
    gates.json
    policy_snapshot.json
    hashes.json
```

### 8.1 `manifest.json` (required fields)

```json
{
  "simulacra_version": "0.1.0",
  "run_id": "run_01H...",
  "created_at": "ISO-8601",
  "task": "natural language task string",
  "sources": [
    { "type": "folder", "uri": "inputs/data-room", "content_hash": "sha256:..." }
  ],
  "artifacts": [
    {
      "path": "outputs/table.parquet",
      "kind": "table",
      "schema": [{ "name": "vendor", "type": "string" }],
      "row_count": 1204,
      "content_hash": "sha256:..."
    }
  ],
  "gates": { "status": "pass", "results": [] },
  "prime": {
    "session_id": "...",
    "model": "provider/model",
    "session_path": "..."
  },
  "app": null
}
```

### 8.2 Eval gates (examples)

| Gate | Phase | Pass condition |
| --- | --- | --- |
| `manifest_present` | A+ | Valid manifest JSON |
| `schema_match` | A+ | Artifacts match declared schema |
| `row_count_bounds` | A+ | Min/max rows if specified |
| `no_path_escape` | A+ | No writes outside allowed roots |
| `network_policy` | A+ | No disallowed egress (when enforced) |
| `sql_read_only` | C+ | No DDL/DML against prod roles |
| `tests` | B+ | `pytest` / custom checks green |
| `app_boots` | D+ | App healthcheck / smoke |
| `metric_parity` | C–D | Optional: compare to certified metric |

Gates map onto Prime **autonomous quality gates** where useful; Simulacra owns the gate catalog and records results in `audit/gates.json`.

---

## 9. Architecture

```text
┌─────────────────────────────────────────────────────────┐
│                     Simulacra                            │
│  Branding · Policy · Run layout · Gates · Audit · Deploy │
└─────────────┬───────────────────────────────┬───────────┘
              │ launches / configures         │ reads artifacts
              ▼                               ▼
┌──────────────────────────┐      ┌──────────────────────┐
│   Prime Agent (pure)     │      │  Outputs + App       │
│   TUI / RPC / JSON / ACP │      │  Parquet, app/, URL  │
│   IPython · rlm · daemon │      └──────────────────────┘
└──────────────────────────┘
```

### 9.1 Ownership split

| Concern | Owner |
| --- | --- |
| LLM loop, IPython, `rlm`, compaction, daemon | **Prime** |
| Plan Q&A, app code edits, iteration diffs (§3A) | **Prime** |
| Launcher branding, default cwd/layout, skills pack | **Simulacra** |
| Source mounts, warehouse tokens, network policy | **Simulacra** |
| Deterministic extract, manifest schema, gates, audit | **Simulacra** |
| Template sync, preview process, deploy keys | **Simulacra** |
| Session persistence + event streaming into console | **Simulacra** wraps **Prime** |
| Job bounds (timeout, steps, cancel, stall) | **Simulacra** |
| `design_brief` capture + injection into tasks | **Simulacra** schema; **Prime** execution |
| Identity / SSO / tenancy | **Simulacra** (later) |

### 9.2 Trust model

Prime workers are **not** a security sandbox. Simulacra must add:

- Disposable worktree / run directory  
- OS-level sandbox or container for untrusted rooms (recommended before external data)  
- Secret injection at process boundary  
- Egress policy  

Document this explicitly in product and security review packs.

---

## 10. Skills & harness (Prime-native)

Ship a **Simulacra skill pack** (Python-backed where needed), not a custom tool router:

| Skill | Purpose |
| --- | --- |
| `simulacra_run` | Read `simulacra.yaml`, enforce output paths |
| `simulacra_manifest` | Write/validate manifest |
| `simulacra_extract` | Common unstructured→table patterns |
| `simulacra_duckdb` | Local tabular analytics (Phase B) |
| `simulacra_warehouse_*` | One WH adapter (Phase C) |
| `simulacra_app_scaffold` | Generate app from template (Phase D) |

Continual harness `/refine` allowed **session-local** for run lessons; **global** promote requires human review.

---

## 11. Security & governance checklist

- [ ] Allowed input roots  
- [ ] Output write jail  
- [ ] No host secrets in prompt/harness by default  
- [ ] Audit pack retained N days (policy)  
- [ ] Human approval before deploy  
- [ ] Gate failure blocks deploy  
- [ ] Optional: pause agent messaging / rate limits for multi-agent runs  
- [ ] Data classification tags on sources and outputs  

---

## 12. Success metrics

| Stage | Metric |
| --- | --- |
| v0 | Time-to-first gate-green table from a sample data room |
| v0 | % runs with valid manifest + audit pack |
| v1 | Repeat run reproducibility (hash-stable extracts under same inputs) |
| v2 | Time-to-internal-app URL from NL task |
| Enterprise | Policy violations caught by gates (not post-incident) |

---

## 13. Non-goals (explicit)

- Replacing Tableau/Looker as the enterprise BI suite  
- Training foundation models  
- Forking or heavily customizing Prime Agent core  
- Multi-cloud warehouse matrix on day one  
- Unrestricted prod write agents  
- Consumer “chat with PDF” without artifacts/gates  

---

## 14. Open decisions

| Decision | Options | Default proposal |
| --- | --- | --- |
| First app template | Streamlit vs FastAPI+HTMX | **Streamlit** (fast internal demos) |
| First warehouse | Snowflake vs BigQuery vs Databricks | Defer until Phase C; pick by design partner |
| Sandbox | Container vs OS sandbox vs worktree-only | **Worktree-only for private alpha**; container before external data |
| Multi-tenancy | Single-tenant deploy first | **Single-tenant / VPC** first |
| Branding of Prime TUI | Wrapper name only vs theme fork | **Launcher + docs branding**; keep Prime TUI until theme API is clean |
| Design brief UI | Collapsed controls vs NL-only | **Collapsed Look & feel + NL merge**; brief always written to JSON for Prime |
| Prime step budget defaults | Conservative vs long builds | **Start conservative** (table in §3A.6); raise per design partner |

---

## 15. Document control

| Version | Change |
| --- | --- |
| 0.1 | Initial spec: brand, progressive data plane, run contract, Prime-pure architecture |
| 0.2 | Canonical UX loop (§3A): prompt→plan→approve→build→iterate; Prime call contract; leave hard work to Prime |
| 0.3 | Prime run lifecycle + anti-loop/interactivity (§3A.6–7); design brief for aesthetics/product choices (§3A.8) |
