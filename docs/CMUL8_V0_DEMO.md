# CMUL8 V0 demo path

This path exercises durable CMUL8 contracts without requiring external model,
connector, cluster, or cloud credentials.

## Local console

```bash
uv sync --extra demo
SIMULACRA_AUTH_REQUIRED=0 uv run uvicorn apps.api.main:app --host 127.0.0.1 --port 8000
cd apps/console && npm ci && npm run dev
```

`SIMULACRA_AUTH_REQUIRED=0` is development-only. Production and private-runtime
configurations must retain authentication and an external secret provider.

Open `http://127.0.0.1:5173`, select an existing project, then open Project Room
from the people icon. The room is created through the tenant-authorized CMUL8
API; tasks, comments, reviews, graph revisions, approvals, and inbox positions
are durable records rather than browser fixtures.

## Verification

```bash
uv run pytest -q
cd apps/console && npx tsc --noEmit && npm run build
```

Validate the lightweight single-VM runtime before release:

```bash
CMUL8_POSTGRES_PASSWORD=test-only SIMULACRA_BOOTSTRAP_PASSWORD=test-only docker compose config -q
terraform -chdir=infra/terraform/modules/aws validate
terraform -chdir=infra/terraform/modules/azure validate
terraform -chdir=infra/terraform/modules/gcp validate
```

## Private-runtime boundary

The image uses `/opt/cmul8/bin/cmul8-entrypoint` for `web`, `api`, `worker`,
`preflight`, `migrations`, and `smoke`. Worker probes contact the running worker
over a Unix socket; readiness additionally verifies queue reachability. The
Compose runtime requires operator-supplied passwords, durable PostgreSQL and Redis
volumes, and a digest-pinned image for staging or production.

Air-gap readiness is documented in `docs/private-runtime/air-gap-readiness.md`;
end-to-end air-gap support is not certified in V0.

## Observability API

Telemetry is ingested and queried per authorized project:

- `POST /projects/{project_id}/cmul8/observability/events`
- `GET /projects/{project_id}/cmul8/observability`
- `GET /projects/{project_id}/cmul8/observability/{kind}/{entity_id}`

The API rejects credential-like telemetry attributes and filters every aggregate
to the requested tenant and project before computing dashboard metrics.
