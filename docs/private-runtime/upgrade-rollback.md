# Upgrade and rollback runbook

Capture the current bundle hash, Helm revision, schema version, and smoke
results before any change. Build an upgrade record with
`create_upgrade_manifest(current, target)`, obtain approval, and verify the
target bundle and image digest independently.

The supported upgrade sequence is verify, preflight, migrate, rolling rollout,
smoke, then promote. Migrations must be idempotent and use expand/contract:
deploy backward-compatible schema additions first, migrate data separately,
then remove old schema only after the previous runtime is outside the rollback
window. The Helm migration Job runs before workloads and stops the rollout on
failure; it does not make destructive migrations safe.

On rollout or smoke failure, stop promotion. Create a rollback record with
`create_rollback_manifest(current, previous, migration_compatible=...)` and get
operator approval. Scale workers down if continued consumption risks mixed
semantics, use `helm rollback RELEASE REVISION --wait`, restore workers, run all
five smoke checks, and record the result. Never roll runtime code behind a
non-backward-compatible schema. Use the database's tested recovery procedure
instead, then roll back the workload. Bundle history is immutable; rollback
selects a previous hash and never edits or rebuilds it.
