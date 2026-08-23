# Backup and disaster recovery boundary

CMUL8 does not provision or operate customer backups. The platform owner must
define and test PostgreSQL point-in-time recovery, Redis durability appropriate
to queue semantics, object-versioning/retention, secret recovery, registry
retention, and cluster rebuild procedures. Record RPO/RTO and dependencies.

A recovery exercise must restore state into an isolated environment, verify the
selected Operational Bundle and image digests, apply the environment preflight,
reconcile migrations, and pass all five smoke checks before DNS cutover. Never
treat the Terraform contract modules as a recovery implementation.

Use the detailed [backup and restore runbook](backup-restore.md). Its evidence
reference is required by the upgrade assessor for schema-changing releases.
