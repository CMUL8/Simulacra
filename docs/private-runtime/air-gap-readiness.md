# Air-gap readiness boundary

This release is **not certified or claimed to support air-gapped operation**.
The bundle builder, verifier, installer, environment preflight without
`--resolve-hosts`, and release-record tooling are stdlib-only and can operate
offline. That is readiness groundwork, not end-to-end support.

An offline evaluation must inventory and mirror the OCI image by digest, Helm
chart, Operational Bundle, signature/trust material, SBOM/provenance, base-image
and OS-package evidence, connector dependencies, and recovery documentation.
Verify every mirrored hash on both sides of the transfer boundary. Use an
internal registry, DNS, time source, certificate chain, secret provider,
PostgreSQL, Redis, and object store.

Known blockers must be resolved and tested before any support claim: build-time
downloads, runtime model/provider calls, connectors requiring public APIs,
revocation and vulnerability-feed refresh, license/update distribution, time
synchronization, and support-bundle transfer policy. The chart deliberately
leaves egress customer-defined and therefore does not prove network isolation.
Test installation, upgrade, rollback, restore, and connector behavior in the
actual disconnected environment; a successful connected-cluster test is not
evidence of air-gap support.
