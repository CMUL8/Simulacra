# CMUL8 private runtime

CMUL8 ships one immutable Operational Bundle for `cmul8_cloud`,
`dedicated_cloud`, and `private_cloud`. A release is never regenerated for a
target. Target-specific endpoints, tenant identity, credentials, ingress, and
capacity enter only through the runtime environment and Helm values.

## Customer/platform responsibilities

Before installation, provide a Kubernetes 1.26+ cluster, external PostgreSQL,
Redis, object storage, a secret provider synchronized to a Kubernetes Secret,
an OCI registry, DNS/TLS, backups, monitoring, and egress policy. The Terraform
modules under `infra/terraform/modules` validate references to these services;
they provision nothing.

## Install

1. Obtain the `.tar`, its SHA-256 digest through an independent channel, and—if
   your release policy requires one—a signature plus verifier implementation.
2. Run `python -m deploy.bundle verify BUNDLE --expected-hash SHA256`. A signed
   bundle cannot be accepted without an injected verifier through the Python
   API. Key distribution and trust policy are intentionally outside this code.
3. Populate the environment contract in `deploy/environment-contract.json` and
   run `python -m deploy.environment environment.json`. Host resolution is
   optional and must be run from the target network with `--resolve-hosts`.
4. Mirror the OCI image by digest. Create the external runtime Secret; do not
   put credentials in values files or the Operational Bundle.
5. Run `helm upgrade --install --atomic --wait --timeout 15m` with
   tenant/environment, immutable image digest, external secret, ingress, TLS,
   service-account annotations, resources, and replicas. Preflight runs before
   the migration Job; either failure stops the rollout.
6. Inject checks for API, worker, queue, storage, and connectors into
   `deploy.run_smoke_checks`. Promote only if all five pass. The optional Helm
   smoke hook is disabled until the image implements the configured internal
   health endpoints; enabling it runs after install, upgrade, and rollback.

The image must implement `deploy/processes.json`: its entrypoint dispatches one
explicit process argument to the web, API, worker, preflight, migration, or smoke binary.
The chart does not retrofit this contract onto the repository's development
image. Validate labels, non-root execution, read-only-root compatibility,
shutdown behavior, ports, and probes in release CI before promotion.

Installation verification rejects tampering, target-specific manifests,
undeclared files, duplicate members, absolute/traversal paths, links, special
files, suspected credential material, and a mismatched content-addressed name.

See [upgrade and rollback](upgrade-rollback.md), [release promotion](release-promotion.md),
[installation hardening](installation-hardening.md), [backup and restore](backup-restore.md),
[air-gap readiness boundary](air-gap-readiness.md), and [support bundles](support.md).
