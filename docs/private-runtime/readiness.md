# Private deployment readiness

Missions separates two operator decisions:

- **Startup ready** means the deployment is safely scoped to one tenant and its
  authentication, database, queue, writable storage, and certified agent
  executor are available. Docker Compose runs this gate automatically before
  migrations.
- **Production ready** adds recorded evidence for a coordinated backup and a
  successful restore drill. This is the decision to use before opening the
  deployment to humans.

## Automatic startup gate

`docker compose up` runs the one-shot `preflight` service after PostgreSQL and
Redis become healthy. Migrations, the API, and the worker cannot start when the
gate is blocked. Inspect the decision with:

```bash
docker compose logs preflight
```

The report names failed capabilities and safe remediation. It never includes
service URLs, filesystem paths, credentials, exception text, or secret values.

## Production decision

Record customer-approved evidence references in `.env` after the backup and
restore drill:

```dotenv
CMUL8_BACKUP_REFERENCE=backup/change-42
CMUL8_RESTORE_TEST_REFERENCE=restore-drill/change-41
```

Then run the operator check from the same immutable image and environment:

```bash
docker compose run --rm api doctor --format human
```

For release automation, request the versioned JSON report:

```bash
docker compose run --rm api doctor --format json
```

Exit status `0` means production ready. Exit status `2` means the deployment is
blocked from production approval. The JSON format is
`missions.private-readiness.v1`; automation should evaluate
`production_ready` and retain the complete report with the release approval.

Evidence references are identifiers only. Do not put credentials, backup data,
internal URLs, or secret-manager values in them.
