# Missions V0

A Mission is a durable, project-scoped coordination record. Existing projects remain
unchanged until an owner or admin creates `POST /projects/{project_id}/mission`.
Mission data is stored under the existing control-root convention in atomic JSON files,
scoped by tenant and project.

The public runtime is always Codex. Agent configuration contains only a role, mandate,
responsibilities, data scope, tools, autonomy, escalation actor, and budget. Provider,
model, credentials, runtime selection, and computer selection are rejected at the API
boundary. The server selects a non-secret execution profile (`routine`, `balanced`,
`deep`, `code`, or `verification`) and records its resolved metadata on each Run.

Mission and agent budgets are a strict V0 contract: optional integer
`max_steps` (1–100) and `wall_timeout_seconds` (1–600) only. Unknown keys,
booleans, floats, strings, and out-of-range values are rejected. Each Codex
turn receives the most restrictive Mission/agent/server cap; that effective
budget is included in the prompt, durable execution binding, and audit event.
Codex enforces the tool cap at the N+1 lifecycle start notification, before
that tool can complete; completion-only protocol streams are bounded
conservatively as a fail-closed fallback.

Cron automation is evaluated automatically by the durable worker on a bounded cadence
(15 seconds by default, configurable from 5–300 seconds). A due occurrence is enqueued
only when the workspace's current Operation Graph revision has an exact approval. The
worker pins that graph head while atomically recording the same contract hash on the
Mission and Run. If approval is missing, the occurrence remains unhandled and is retried
after approval. Repository locking plus the durable occurrence ledger prevents duplicate
Runs across repeated ticks or multiple worker replicas.

The scheduler runs in its own stoppable worker thread, so a long runtime job or health
probe cannot delay cron discovery. Automatic queues apply bounded backpressure at 128
active Runs. Each trigger retains its latest 128 handled occurrences, and unreferenced
terminal Run history retains the latest 256 records. Active Runs and terminal Runs still
referenced by approvals, deliverables, trajectory events, or the retained occurrence
ledger are never pruned.

Worker discovery reads a separate atomic, scope-only `discovery.json` index rather than
the evidence-bearing `state.json`. The index remains tiny and descriptor-safe even when
human-verification evidence and deliverables must be retained beyond 8 MiB; the full
repository still validates its tenant/project scope before scheduler or consumer use.
Legacy bounded state without an index is accepted for one migration cycle; its next
repository mutation publishes the index. Oversized evidence state is never used as a
discovery document.

Condition automation remains explicitly fact/event-driven in V0: clients call
`POST .../automation/evaluate-due` with typed facts. Conditions are a bounded
`fact`/`operator`/`value` comparison and never execute code or invoke a model. Empty
facts never fire a condition. Repeating an occurrence creates no duplicate Run.
Cron uses numeric five-field expressions: wildcards, numbers, lists, ranges, and
steps (`*/N` or `A-B/N`); day-of-week accepts both `0` and `7` for Sunday.

Mission Runs are durable queued records. The worker invokes the server-selected Codex
profile only after it verifies the exact approved Operation Graph snapshot.

Deliverables are immutable versions identified by SHA-256. Verification binds an exact
hash and optimistic revision, is limited to the Mission owner or designated verifier,
and rejects producer self-verification. Creating a newer version requires that new
version to be verified independently.
# Mission V0 execution

Mission Runs are durable, Codex-only work items. The worker atomically leases one queued run, checks the exact currently approved Operation Graph immediately before each agent turn, and records a durable trajectory, checkpoint decisions, session identities, and server-read artifact evidence.

Production Mission execution additionally requires `CMUL8_MISSION_ISOLATION_LAUNCHER` to name the baked `/opt/cmul8/bin/cmul8-mission-sandbox` launcher. Each turn gets a random, one-shot `0600` manifest below `CMUL8_MISSION_RUNTIME_ROOT` (default `/app/data/mission-runtime`), outside tenant workspaces and `/app/runs`. It binds exact canonical read/write roots, Mission/run/agent/invocation IDs, the approved execution-binding digest, the operator-owned model route, a private temporary root, a durable private Codex home scoped to tenant/project/Mission/agent, and the `/opt/codex` executable. The launcher re-hashes its descriptor-safe manifest before applying Landlock; it grants only `/usr`, the Codex runtime, declared roots, the private state/temp roots, and exact TLS/DNS files. The inner Codex sandbox never receives the state-home path as a workspace root. State is retained for Mission continuity; retention/cleanup policy is a post-V0 operational control. Its child environment is rebuilt from the credential names allowed by the bound route, with `HOME`, `CODEX_HOME`, and `TMPDIR` set to private roots. If any check is absent or unsafe, the run stops at `sandbox_unavailable` and Codex is never started.

The launcher accepts only the pinned strict Codex app-server argument sequence. Mission startup rejects any active project `.codex` configuration or project-origin effective setting, plugins, apps, hooks, multi-agent delegation, skill search and skill-provided MCP installation; verifies that the effective MCP inventory is empty; enumerates and disables every loaded skill; and fails closed unless a forced reload confirms that all skills remain disabled. The managed runtime supports the official OpenAI route and an operator-configured HTTPS endpoint that implements the OpenAI Responses API, including open-weight models served behind that contract. Provider and model are never user-selectable; their safe identities are bound into every turn. The runtime keeps only the selected route's credential in its own process, while its shell environment inherits none of it. A configured credential must be present or the run is durably gated as `credential_unavailable` before any invocation starts. Compose supplies the baked launcher and fixed runtime-root defaults; deployments must not substitute an ordinary wrapper.

Runs poll through the Mission Pod; this is persisted-state polling, not token streaming. A checkpoint agent waits for an owner or admin decision before Codex is invoked. An expired lease after the provider start marker is intentionally held for human retry, because its side effects are uncertain. Only queued or approval-waiting runs can be cancelled.

Artifacts are read descriptor-relatively beneath the project workspace, reject links/traversal and files over 16 MiB, and become `awaiting_verification` deliverable candidates. The trajectory export is member-readable and contains only bounded, secret-screened durable values.

`code.write` never receives the canonical `app/` directory as a write root.
It writes only to an empty attempt-unique per-run
`outputs/missions/<run>/<agent>/code-staging-r<revision>` directory, so retries
never delete or reuse an earlier candidate. Each staged code file records its intended `app/` target and hash;
an owner or designated verifier must verify that exact file before a descriptor-
safe atomic single-file promotion updates the canonical app. Failed-turn staged
candidates remain reviewable and require that same explicit verification. Until
then, the preview continues to serve only the existing verified app.
