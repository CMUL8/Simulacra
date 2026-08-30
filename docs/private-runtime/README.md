# CMUL8 private runtime

CMUL8 V0 uses Docker Compose on a single Linux VM. This is the smallest honest
production shape for the current product: PostgreSQL, Redis, a one-shot migration
process, the API/web process, and one worker. It avoids operating a cluster before
CMUL8 needs multi-node scheduling.

## Host responsibilities

Provide Docker Engine with Compose v2, at least 4 CPU cores and 8 GB RAM, durable
disk, DNS/TLS through a host reverse proxy or load balancer, backups, monitoring,
and outbound access for enabled model providers and connectors. The application
port binds to `127.0.0.1` by default.

## Install

1. Copy `.env.example` to `.env` and set long random values for
   `CMUL8_POSTGRES_PASSWORD` and `SIMULACRA_BOOTSTRAP_PASSWORD`.
2. For a release, set `CMUL8_IMAGE` to a digest-pinned image. A local evaluation
   may omit it and build `cmul8:local` from the repository.
3. Validate configuration with `docker compose config -q`.
4. Start with `docker compose up -d --build`.
5. Confirm the one-shot preflight and migration services exited zero, then confirm
   PostgreSQL, Redis, API, and worker are healthy.
6. Record backup/restore-drill references and run `cmul8-doctor` before production
   approval.
7. Run the full smoke suite before promotion.

The API and worker use a read-only root, drop Linux capabilities, set
`no-new-privileges`, and write only to `/tmp`, `/app/data`, and `/app/runs`.
PostgreSQL and Redis are not published on host ports.

Kubernetes is deliberately not a V0 dependency. Reconsider it only after CMUL8
needs multi-host failover, independent autoscaling, or a customer explicitly
requires cluster-native installation.

See [environment](environment.md), [installation hardening](installation-hardening.md),
[upgrade and rollback](upgrade-rollback.md), [backup and restore](backup-restore.md),
[private deployment readiness](readiness.md), [air-gap readiness](air-gap-readiness.md),
and [support bundles](support.md).
