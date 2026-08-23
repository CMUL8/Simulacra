# Air-gap readiness boundary

This release is not certified for air-gapped operation. Bundle verification and
release-record tooling can run offline, but builds currently download base images,
OS packages, Python/Node dependencies, and the configured builder agent. Runtime
model providers and many connectors also require outbound access.

An offline evaluation must mirror every OCI image by digest, the Operational Bundle,
signature/trust material, SBOM/provenance, and recovery documentation. Provide an
internal registry, DNS, time, certificate chain, PostgreSQL, and Redis. Test install,
upgrade, rollback, restore, and connector behavior inside the disconnected network.
