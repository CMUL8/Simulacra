# Upgrade and rollback runbook

Capture the current bundle hash, Helm revision, schema version, and smoke
results before any change. Run `assess_upgrade(current, target)` and stop on any
error. Schema-changing upgrades require a backup reference and evidence from a
tested restore; rollback is permitted only when the migration declares backward
compatibility. Build an upgrade record with
`create_upgrade_manifest(current, target)`, obtain approval, and verify the
target bundle and image digest independently.

The supported upgrade sequence is verify, preflight, migrate, rolling rollout,
smoke, then promote. Migrations must be idempotent and use expand/contract:
deploy backward-compatible schema additions first, migrate data separately,
then remove old schema only after the previous runtime is outside the rollback
window. Use `helm upgrade --atomic --wait`; the preflight hook runs at weight
`-20`, then migration at `-10`. Either hook stops the rollout. The migration Job runs before workloads and stops the rollout on
failure; it does not make destructive migrations safe.

On rollout or smoke failure, stop promotion. Create a rollback record with
`create_rollback_manifest(current, previous, migration_compatible=...)` and get
operator approval. Scale workers down if continued consumption risks mixed
semantics, use `helm rollback RELEASE REVISION --wait`, restore workers, run all
five smoke checks, and record the result. Never roll runtime code behind a
non-backward-compatible schema. Use the database's tested recovery procedure
instead, then roll back the workload. Bundle history is immutable; rollback
selects a previous hash and never edits or rebuilds it.

Do not assume `--atomic` reverses database changes. It rolls Kubernetes release
state back; database recovery follows the tested restore runbook when the
migration is not backward compatible.
