# CMUL8 V0 foundation contract

Status: frozen for Wave 1  
Branch: `codex/v0-integration`  
Scope source: the replacement CMUL8 multiplayer brief supplied 2026-08-23

## Architecture map

Simulacra is already a two-process control plane:

- `apps/console`: React/Vite conversational console, preview, activity, versions,
  governance and administration.
- `apps/api`: FastAPI boundary for auth, tenancy, projects, jobs, chat, build,
  preview, deploy, audit and SSE.
- `simulacra/demo`: current product services. Projects are durable filesystem
  workspaces; identity can use JSON or Postgres. Jobs are server-side and events are
  append-only JSONL with live fan-out.
- `simulacra`: Prime/RPC launch and lower-level execution utilities.
- `templates`: generated React artifact chassis.
- `schemas`: existing permissive run manifest contract.
- `tests`: deterministic API/service tests.

CMUL8 extends this shape rather than replacing it. The console and API remain the
control plane. Generated operational bundles contain an independent runtime plane.

## Existing capability matrix

| Capability | Baseline | V0 direction |
| --- | --- | --- |
| Persistent projects/chat | Present | Become Project Rooms with members, tasks and durable events |
| Prime builder | Present and retained | Move behind `PrimeHarness` |
| Codex builder | Missing | Default `CodexHarness`; fail clearly when SDK/app-server is unavailable |
| Background jobs/cancel | Present | Normalize through harness budgets and events |
| Streaming activity | JSONL + SSE | Add durable actor/correlation envelope without breaking legacy events |
| Operation manifest | Present, decorative/permissive | Add executable, versioned Operation Graph alongside migration adapter |
| Preview/checkpoints | Present | Preserve last healthy preview and make version gates explicit |
| Tenancy/RBAC/audit | Present | Apply to collaboration, review and consequential actions |
| Runtime workflow/action plane | Missing | Wave 2 consumer of approved Operation Graph |
| Cloud/private packaging | Docker/Fly baseline | Wave 2/3 immutable bundle and hardened Compose runtime |
| Multiplayer UI | Missing | Feature modules in Wave 2; no global redesign |

## Gap and risk analysis

1. Prime-specific execution is coupled to chat/build services. Adapters must preserve
   old call sites while Codex becomes the configured default.
2. Existing events are user-facing build events, not a complete durable domain event
   model. New envelopes must coexist with the current SSE shape.
3. Project state is filesystem-oriented. Wave 1 uses append-only/project-scoped stores
   and stable repository interfaces so a Postgres implementation can be added without
   changing product contracts.
4. Operation Graph approval must gate builds and material permission/connector growth;
   it cannot be inferred from chat prose.
5. Runtime agents are data/action actors only. No runtime interface exposes a source
   write capability.
6. Existing visual baseline clips horizontally at 1280x800. UI work must fix this and
   preserve the calm, dense visual language at both 1280x800 and 1440x900.

## Frozen shared contracts

### Harness

`simulacra.harnesses` owns the provider-neutral contract.

- `AgentHarness`: `create_session`, `resume_session`, `run`, `cancel`,
  `stream_events`, `healthcheck`, `capabilities`.
- `AgentRunRequest` includes project/environment/workspace, prompt, role, task type,
  read/write roots, network policy, timeout, step budget, harness/provider/model
  configuration and trace context.
- `AgentRunResult` includes harness/provider/model/session, terminal status, response,
  structured output, changed files, normalized events, duration, usage and error.
- Task types are the exact brief values: chat, architect, build_app, build_workflow,
  configure_agent, qa, research, iterate and repair.
- Terminal statuses: succeeded, failed, cancelled and timed_out.
- Default selection is `CMUL8_EXECUTION_BACKEND=codex`; non-default executors must be reviewed and baked into the image's source-controlled certified registry, and fallback is never implicit.
- Provider credentials are referenced by environment-variable name only and are never
  serialized into projects or generated bundles.

### Operation Graph

The canonical schema ID is `cmul8.operation-graph.v0`; the serialized example name is
`operation-graph.v0.yaml`. JSON is the validation format; YAML is an optional syntax.

- Required top-level areas: metadata, entities, views, workflows, agents,
  automations, connectors, permissions, approval_rules and schedules.
- Graph revisions are immutable and content-addressed.
- Approval records refer to an exact revision hash and actor.
- Runtime/build consumers accept only validated, approved revisions.
- Graph diff is structural and reports added/changed/removed plus security, migration
  and test impact.
- Existing `manifest.v0` remains readable through an explicit migration adapter.

### Collaboration and tasks

- One `ProjectRoom` per project.
- Task states: proposed, ready, working, in_review, done, blocked, failed, cancelled.
- A task has exactly one accountable owner and zero or more collaborators.
- Work may start only after an atomic claim against the expected task revision.
- Reviews are approve, request_changes, question, reject or rollback.
- Comments may target a project, task or exact Operation Graph element path/revision.
- Mentions are normalized references, never parsed as authorization.
- Presence is ephemeral; meaningful activity, tasks, comments, review and approvals are
  durable.

### Durable event envelope

New domain events use:

`id`, `actor_type`, `actor_id`, `tenant_id`, `project_id`, `task_id`,
`operation_graph_version`, `application_version`, `environment_id`, `action`,
`result`, `timestamp`, `correlation_id`, `trace_id`, `payload`.

`actor_type` is human, builder_agent, runtime_agent or system. Events are append-only,
tenant/project scoped and idempotent by `id`. The collaboration adapter may project
them to the legacy `type/label/detail/status` SSE shape; it must not mutate the source
event.

### Runtime service boundary (Wave 2 consumer)

- Entity repository: validated CRUD/query scoped by tenant, environment and graph.
- Workflow service: idempotent transition command with expected state/version.
- Approval service: request/decide/expire; requester cannot self-approve when policy
  forbids it.
- Action gateway: the only external-write boundary; consequential actions default to
  pending approval.
- Runtime-agent gateway: tool/data/action allowlist only; no source filesystem write.
- Scheduler: durable trigger claim, retry/backoff and dead-letter contract.

### HTTP and frontend conventions

- New API routes are rooted at `/v1/projects/{project_id}`; cross-project inventory is
  `/v1/activity` or `/v1/observability`.
- JSON fields use snake_case. IDs use stable prefixes (`room_`, `task_`, `comment_`,
  `evt_`, `ogr_`, `approval_`).
- Mutating requests accept an expected revision/version and reject stale writes with
  conflict semantics.
- Frontend modules live below `apps/console/src/features/{project-room,operation-graph,
  activity,team,observability}` and consume typed API adapters. They do not own global
  navigation, root state or broad CSS.

### Persistence and migration convention

- Wave 1 stores are repository interfaces with deterministic project-scoped JSON/JSONL
  implementations under the project audit/work area.
- Writes are atomic (`temp -> fsync where supported -> replace`) or append-only.
- Each durable record carries `schema_version`, `tenant_id`, `project_id`, `revision`,
  `created_at`, `updated_at` where applicable.
- Database migrations, when introduced, use ordered immutable files and remain
  integration-lead owned.

## Workstream ownership and dependency graph

Wave 1 starts from this document and the integration branch baseline:

1. Operation Graph: `schemas/operation-graph*`, `simulacra/operation_graph/**`, graph
   fixtures/tests. No UI/runtime edits.
2. Collaboration: `simulacra/collaboration/**`, collaboration fixtures/tests. It may
   import the frozen event contract but does not edit current screens or API routing.
3. Harness: `simulacra/harnesses/**`, harness tests. It may wrap Prime behavior but
   does not rewrite `pipeline.py`, job routing or API entry points.

Merge/integration order is Operation Graph -> Collaboration -> Harness. Wave 2 runtime,
UI and deployment depend on Wave 1. Wave 3 observability depends on the approved runtime
and UI contracts.

## Test and visual baseline

- Python: `.venv/bin/pytest -q` -> 84 passed.
- Console: `npm run build` in `apps/console` -> Vite production build succeeded.
- Desktop baseline: `artifacts/ui-baseline/landing-1440x900.png`.
- Laptop baseline: `artifacts/ui-baseline/landing-1280x800.png`. The initial full-page
  browser capture appeared horizontally offset because fixed layers were composited
  incorrectly by that capture mode. A viewport capture plus DOM measurements confirmed
  a 1280px document with centered 680px content and no horizontal overflow; the verified
  Wave 2 capture is `artifacts/ui-baseline/wave2-landing-viewport-1280x800.jpg`.

No baseline product test failure was observed. Untracked user files present before this
work are out of scope and must be preserved.
