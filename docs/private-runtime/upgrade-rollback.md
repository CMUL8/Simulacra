# Upgrade and rollback runbook

Capture the current bundle hash, image digest, runtime version, schema version,
backup reference, and smoke results. Run `assess_upgrade(current, target)` and stop
on any error. Schema-changing upgrades require a backup plus tested restore evidence.

For upgrade:

1. Pull the digest-pinned target image and verify the target bundle.
2. Run preflight and take a database/volume backup.
3. Run `docker compose run --rm migrate`.
4. Run `docker compose up -d --no-build api worker`.
5. Wait for health checks, run the full smoke suite, then record promotion.

For rollback, stop the worker if mixed-version consumption is unsafe, set
`CMUL8_IMAGE` to the previous digest, and recreate API and worker. Never roll code
behind a non-backward-compatible schema; restore the tested database backup first.

Compose does not make database changes atomic. Expand/contract migrations and tested
recovery remain mandatory.
