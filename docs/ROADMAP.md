# Simulacra — Roadmap & Execution Plan

**Companion to:** [PRODUCT_SPEC.md](./PRODUCT_SPEC.md) · **Maker law:** [APP_MAKER_CONTRACT.md](./APP_MAKER_CONTRACT.md)  
**North star:** Governed vibe coding for internal data apps — NL → production analytics apps, with persistent execution, eval gates, and audit trails.  
**Engine:** Prime Agent (pure). Simulacra = brand + policy + run contract + gates + deploy.

---

## Strategy in one paragraph

Ship the **same loop** four times with richer sources and sinks:  
**(A) folder research → gated tables** → **(B) local tabular/DuckDB** → **(C) warehouse read** → **(D) deployable internal app**.  
Do not build warehouse or multi-template deploy before the run contract and gates are real.

**UX north star:** prompt → **bootstrap preview (template)** → refine → **Improve with Prime** → iterate → ship. See APP_MAKER_CONTRACT. Simulacra owns rails/gates/job bounds; Prime owns taste and code. Console stays interactive (cancel, SSE, one job at a time).

---

## Phase map

| Phase | Name | User-visible outcome | Depends on |
| --- | --- | --- | --- |
| **0** | Foundation | Branded launcher + run layout + Prime wired | — |
| **A** | Data rooms | NL research on folders → parquet/CSV + audit | 0 |
| **B** | Tabular lab | DuckDB analytics on outputs + stronger gates | A |
| **C** | Warehouse | One read-only WH connector + query audit | B |
| **D** | Ship apps | One app template + approve-to-deploy | B (C optional) |
| **E** | Enterprise hard | SSO, tenancy, sandbox, retention, admin | C–D |

Phases **C** and **D** can partially overlap once **A/B** are solid; **D** can demo on DuckDB without warehouse.

---

## Phase 0 — Foundation (1–2 weeks)

### Goals
- Repo identity is Simulacra (not “Python RPC wrapper demo”).
- `simulacra init|run|status|audit` stubs.
- Opinionated run directory + `simulacra.yaml`.
- Launch **pure** Prime with cwd = run dir and Simulacra skill pack path.

### Deliverables
- [ ] README + brand (this docs set)
- [ ] `simulacra.yaml` schema (task, sources, gates, limits)
- [ ] Run directory scaffold CLI
- [ ] Launcher: resolve `prime-agent` / local clone; set env; exec TUI or `-p`
- [ ] Skill pack skeleton: `simulacra_run`, `simulacra_manifest`
- [ ] Policy defaults: write jail under `outputs/`, documented trust model

### Exit criteria
- Engineer can `simulacra init` → `simulacra run` → Prime opens in the run folder with skills loaded.

### Non-goals
- Web UI, warehouse, deploy, multi-tenant.

---

## Phase A — Unstructured data rooms (2–4 weeks)

### Goals
Prove: **shared folder + research task → structured table + summary + audit**.

### Deliverables
- [ ] Folder source mount into `inputs/` (copy or bind; record content hash)
- [ ] Extraction skill patterns (PDF/text/CSV/JSON → rows)
- [ ] Required `outputs/manifest.json` writer + validator
- [ ] Gates: `manifest_present`, `schema_match`, `row_count_bounds`, `no_path_escape`
- [ ] `simulacra gate` / post-run gate runner (can wrap Prime autonomous gates)
- [ ] `simulacra audit export` → zip of `audit/` + manifest
- [ ] 2 golden fixtures (small public data rooms) + CI checking gate-green

### Reference workflow
1. User drops files in a data room.  
2. `simulacra init --source ./room --task "..."`  
3. Agent runs (interactive or headless RPC/JSON).  
4. Gates run.  
5. User receives `outputs/table.parquet` + `summary.md` + audit pack.

### Exit criteria
- Cold start on fixture → gate-green in one documented command path.  
- Second run on same inputs produces comparable schema (row counts may vary if LLM extraction — document determinism limits; prefer code-defined extract where possible).

### Risks
- LLM-only extraction = flaky gates → mitigate with hybrid: agent writes **code** that extracts, gates check code outputs.

---

## Phase B — Tabular lab (2–3 weeks)

### Goals
Treat Phase A outputs (and uploaded tables) as a **local analytics workspace**.

### Deliverables
- [ ] DuckDB skill + conventions (`outputs/warehouse.duckdb` or parquet lake)
- [ ] Gates: `tests` (pytest in run), basic data quality (null rates, PK uniqueness optional)
- [ ] Optional: generate exploratory notebook or SQL pad as artifact
- [ ] Thin “app preview” scaffold (not full deploy) — e.g. Streamlit read-only over parquet

### Exit criteria
- Run can: extract → load DuckDB → answer metric-style questions → emit updated tables with gates green.

---

## Phase C — Warehouse connection (3–5 weeks)

### Goals
One **read-only** warehouse path with secrets outside the model.

### Deliverables
- [ ] Pick design-partner warehouse (Snowflake **or** BigQuery **or** Databricks)
- [ ] Connector skill + Simulacra-side credential injection
- [ ] Query log → `audit/queries.jsonl`
- [ ] Gates: `sql_read_only`, optional `metric_parity`
- [ ] Materialize extracts to `outputs/` for offline reproducibility
- [ ] Security review checklist signed off

### Exit criteria
- Agent can only use injected read role; failed DDL attempt is gated/blocked.  
- Audit shows query + user/run binding.

### Non-goals
- Writeback to warehouse; multi-WH matrix; semantic layer completeness.

---

## Phase D — Deployable apps (3–5 weeks)

### Goals
**Approve → internal URL** for a generated app bound to a gate-green run.

### Deliverables
- [ ] Single template: **Streamlit** (default proposal)
- [ ] `simulacra_app_scaffold` skill: app reads manifest + parquet/DuckDB/WH views
- [ ] `simulacra deploy` (start: local process or single VM/k8s service)
- [ ] Human approval step (CLI flag or console later)
- [ ] Release record: `run_id`, image/commit, approver, timestamp
- [ ] Gate: `app_boots` smoke

### Exit criteria
- Demo path: data room **or** DuckDB **or** WH → app URL in <1 hour for a trained user.  
- Redeploy from prior `run_id` without re-prompting (artifact-based).

### Parallel track
- Soft brand: Simulacra web “Runs” list (optional); not required for D exit.

---

## Phase E — Enterprise hardening (in progress)

**Shipped (multi-user / multi-tenant baseline):**
- Auth: register / login, sessions (`sst_`), API keys (`ska_`), bootstrap admin
- Tenancy: workspaces, `X-Tenant-Id`, memberships, RBAC (owner/admin/member/viewer)
- Isolation: projects scoped by tenant; quotas (`max_projects`, concurrent jobs)
- Audit: platform + per-tenant JSONL; project audit zip export
- Sandbox: docker / gVisor (`runsc`) / ephemeral machines (local `--rm` or Fly Machines) / worktree jail
- Identity store: JSON local **or** Postgres (`SIMULACRA_DATABASE_URL`)
- SIEM: CEF / NDJSON / Splunk HEC export + webhook forward (`SIMULACRA_SIEM_WEBHOOK`)
- Deploy shape: `Dockerfile`, `fly.toml`, `docker-compose.yml` (includes Postgres)

**Still open:**
- SSO / OIDC  
- PII classifiers / redaction policies  
- Budgeting (map to Prime goal/autonomy budgets + $ caps)  
- Managed gVisor fleet on every region by default

---

## Milestone timeline (indicative)

```text
Week 0-2    Phase 0 Foundation
Week 2-6    Phase A Data rooms (+ golden fixtures)
Week 6-9    Phase B Tabular lab
Week 8-13   Phase D App deploy (can start on B outputs)
Week 10-15  Phase C Warehouse (design partner driven)
Week 15+    Phase E Hardening
```

Adjust order if the first design partner is warehouse-native: still finish **A contract** before deep WH work.

---

## Team shape (minimal)

| Role | Focus |
| --- | --- |
| Founding eng | Launcher, run contract, gates, Prime integration |
| Data/platform eng | Connectors, DuckDB, WH, audit |
| Design partner (customer) | One real data room + one app target |

Avoid building a large web IDE before A/B work.

---

## Build vs buy / reuse

| Need | Approach |
| --- | --- |
| Agent runtime | **Reuse Prime** (binary or source launcher) |
| Orchestration RPC | Optional thin Simulacra CLI; avoid rewriting RpcClient unless Python ops require it |
| App host | Start process supervisor / simple k8s Deploy |
| Auth | Sessions + API keys + RBAC (SSO later) |

---

## Branding rollout checklist

- [ ] README = Simulacra product, not wrapper tour  
- [ ] `docs/PRODUCT_SPEC.md` + `docs/ROADMAP.md` linked  
- [ ] CLI name `simulacra`  
- [ ] Skill pack namespace `simulacra_*`  
- [ ] GitHub repo description = tagline  
- [ ] Prime attribution in docs (“Powered by Prime Agent”)  
- [ ] Do not claim warehouse/prod deploy until Phase C/D exit  

---

## Immediate next actions (this week)

### Product loop (highest leverage)

1. **Persistent Prime sessions** per project (drop `no_session=True` on product path; store session id in state/manifest).  
2. **Iterate = builder** — follow-ups that change UI must call `prime_build_app` / scoped `run`, not chat-only + heuristic.  
3. **Single build `run`** — merge config into the build session; one cold start max per Approve.  
4. **Bounded jobs** — timeout + max steps + stall detection + **Stop**; one builder per project; UI stays interactive (SSE).  
5. **`design_brief`** — capture Look & feel / IA / must_not; write JSON; inject into every Prime build/iterate task.  
6. **Plan evidence** — allow Prime light tool use on `inputs/` + plan preview during plan chat.  
7. **Honesty in UI** — label Prime vs heuristic; surface `prime.error` / timeout / cancelled.

### Foundation (still open)

8. Freeze **v0 run layout** + `manifest.json` schema in repo (`schemas/`) including `design_brief`.  
9. Skill pack wired into Prime `--skill` (`simulacra_run`, `simulacra_manifest`, `simulacra_app_scaffold`).  
10. Golden fixture CI for gate-green + optional Prime-on smoke with forced timeout test.  

---

## Decision log

| Date | Decision |
| --- | --- |
| 2026-08-06 | North star = governed internal data apps; engine = Prime pure |
| 2026-08-06 | v0 source = unstructured folders / data rooms, not warehouse |
| 2026-08-06 | Progressive plane: unstructured → tabular → WH → deployable apps |
| 2026-08-06 | Proposed first app template = Streamlit; first WH TBD by partner |
| 2026-08-07 | Canonical UX = plan→approve→Prime build→Prime iterate (§3A); Simulacra rails, Prime hard work |
| 2026-08-07 | Demo template path remains React command-center until Phase D Streamlit decision is revisited |
| 2026-08-07 | Follow-up must mutate `app/` via Prime `run`; chat-only refine is a bug |
| 2026-08-07 | Prime jobs are bounded (timeout/steps/cancel/stall); no auto-chain refine without user message |
| 2026-08-07 | User aesthetics/IA travel as structured `design_brief` into every builder task |
| 2026-08-07 | Implemented §3A control plane: JobManager, design_brief, async approve, Stop, honesty labels |
| 2026-08-07 | Multi-tenant admin + sandbox (docker|worktree auto); keep-demo supervisor for API+console |
