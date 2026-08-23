# Backup and restore runbook

This runbook coordinates customer-managed services; CMUL8 does not create or
certify backups.

## Before a change

Record the bundle, image digest, chart revision, schema version, tenant, and
environment. Confirm PostgreSQL PITR coverage includes the change window,
object versioning/retention is active, required Redis durability is understood,
registry digests are retained, and secret-provider recovery is documented.
Create a change-record reference; a snapshot identifier alone is not restore
evidence.

## Restore drill

Restore PostgreSQL and required objects into an isolated network under new
names. Restore or recreate secrets through the customer's approved mechanism;
never copy them into a support bundle. Deploy the exact recorded bundle/image,
run preflight, reconcile only migrations valid for the restored schema, and run
API, worker, queue, storage, and connector smoke checks. Verify tenant isolation
and audit continuity. Destroy the isolated copy according to data-retention
policy and record timestamps, observed RPO/RTO, hashes, results, and approver.

## Incident restore

Freeze writes and queue consumption, preserve forensic evidence, choose a
recovery point with the data owner, restore customer-managed state, and verify
hashes before workload start. Keep ingress closed until preflight and all smoke
checks pass. Reconcile queued work for duplicates before workers resume. Record
data loss, downtime, approvals, and the restore-drill reference. DNS cutover and
failback are platform-owner actions.
