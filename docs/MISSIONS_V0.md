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

Automation is explicit in V0: clients call `POST .../automation/evaluate-due` with
typed facts. Cron expressions are evaluated deterministically; conditions are a bounded
`fact`/`operator`/`value` comparison and never execute code or invoke a model. Repeating
an occurrence creates no duplicate Run.
Cron uses numeric five-field expressions: wildcards, numbers, lists, ranges, and
steps (`*/N` or `A-B/N`); day-of-week accepts both `0` and `7` for Sunday.

Mission Runs are durable queued records in this slice. They capture the server-selected
Codex execution profile and contract snapshot, but do not yet invoke an agent; execution
orchestration is the next integration.

Deliverables are immutable versions identified by SHA-256. Verification binds an exact
hash and optimistic revision, is limited to the Mission owner or designated verifier,
and rejects producer self-verification. Creating a newer version requires that new
version to be verified independently.
