# Vendor onboarding reference

This executable reference composes the approved Operation Graph, collaboration,
durable runtime, human approvals, ActionGateway, runtime-agent policy, audit, and
telemetry contracts into one vendor-onboarding workflow.

It demonstrates:

- vendor intake and document records scoped to one tenant/environment/project;
- deterministic workflow transitions through document and risk review;
- a read-only risk-exception runtime agent with no source, filesystem, process, or
  credential access;
- both collaboration self-review denial and runtime approval self-decision denial;
- notification writes only through ActionGateway, with an opaque recipient reference;
- connector-side idempotency, durable retry/backoff, audit events, and telemetry;
- restartable JSON state independent of the control plane after graph approval.

Run the successful scenario:

```bash
PYTHONPATH=. python examples/vendor-onboarding/run.py
```

Run the deterministic provider-failure/retry scenario:

```bash
PYTHONPATH=. python examples/vendor-onboarding/run.py --fail-first-notification
```

`--state-dir` may point to a fresh directory when you want to inspect the resulting
Operation Graph, collaboration, runtime, audit, approval, action, and telemetry files.
No network client or raw credential is included; notification delivery is an injected
idempotent connector executor.
