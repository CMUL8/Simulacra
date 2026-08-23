# CMUL8 usable V0 checklist

V0 is usable when a new team can describe a generic business process, review and
approve its Operation Graph, build an operational application, run the workflow
with humans and runtime agents, deploy it on one VM, observe it, and safely change
or roll it back. No fixed industry template is required.

## P0 — one complete product loop

- [ ] Route planning and building through the configured builder harness; Codex is
  the default and Prime is an optional adapter.
- [ ] Generate an Operation Graph from the project requirement and require approval
  of its exact revision before building.
- [ ] Make the build consume the approved graph and produce working entity CRUD,
  workflow transitions, tasks, approvals, permissions, and audit events.
- [ ] Prove the loop with a neutral case/request workflow created from a prompt, not
  a bundled template.

Exit test: a fresh user can go Describe → Plan → Review → Build → Test in the UI
without fixtures, manual file edits, or a CLI-only scenario.

## P0 — real runtime execution

- [ ] Replace the worker health-only loop with Redis queue consumption.
- [ ] Execute deterministic workflow transitions and explicitly permitted runtime
  agent actions from the approved graph.
- [ ] Add leases, retries, idempotency, cancellation, and dead-letter visibility.
- [ ] Emit runtime events into the same observability store used by the console.

Exit test: submitting a record creates durable work, the worker processes it once,
an approval blocks the consequential action, and the UI shows the full trace.

## P0 — multiplayer that updates

- [ ] Add member/invite and inbox-read controls to the console.
- [ ] Stream or poll room revisions, presence, tasks, comments, and reviews.
- [ ] Handle revision conflicts and reconnect without fabricated state.
- [ ] Run a two-browser test covering claim, comment, review, and approval denial.

Exit test: two users see each other's changes without manually reloading.

## P0 — deploy and recover

- [ ] Boot `docker-compose.yml` on a clean Linux VM using a digest-pinned image.
- [ ] Put TLS in front of the loopback-bound API and verify authentication.
- [ ] Implement real API, worker, queue, storage, and connector smoke checks.
- [ ] Test PostgreSQL/data-volume backup, upgrade, rollback, and restore.

Exit test: a verified release survives restart, upgrades safely, and rolls back to
the last healthy version with its audit history intact.

## P1 — product readiness

- [ ] Add browser end-to-end tests for the complete loop and failure recovery.
- [ ] Remove remaining legacy report/dashboard assumptions from generic builder paths.
- [ ] Add empty/loading/error/accessibility states and first-run guidance.
- [ ] Set explicit V0 limits for tenants, projects, file sizes, job concurrency,
  connector access, model budgets, retention, and support diagnostics.

## Release gate

- [ ] Python, TypeScript, production build, browser E2E, container boot, and smoke pass.
- [ ] No test data or sample workflow appears in a new workspace.
- [ ] No consequential action bypasses RBAC, graph approval, or self-approval denial.
- [ ] Known limitations are visible in-product and documented.
