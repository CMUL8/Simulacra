# Installation hardening checklist

- Use a dedicated Linux VM and a current Docker Engine/Compose v2 release.
- Restrict SSH and Docker daemon access; Docker-group membership is root-equivalent.
- Keep `.env` at mode `0600`; never put it in a bundle or support archive.
- Use a digest-pinned `CMUL8_IMAGE` and verify the Operational Bundle hash.
- Keep the API bound to `127.0.0.1`; expose it only through a TLS reverse proxy.
- Do not publish PostgreSQL or Redis ports.
- Retain read-only roots, dropped capabilities, `no-new-privileges`, health checks,
  and durable named volumes in `docker compose config`.
- Back up PostgreSQL plus the `cmul8-data` and `cmul8-runs` volumes, and test restore.
- Run migrations as the one-shot `migrate` service before API and worker startup.
- Promote only after API, worker, queue, storage, and connector smoke checks pass.

The worker health probe must reflect a running queue consumer. Until the worker
actually consumes jobs, container health is plumbing validation—not proof that
automations or runtime agents execute.
