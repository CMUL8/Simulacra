# Release promotion contract

A candidate may advance between environments only when all of these identities
remain unchanged: Operational Bundle SHA-256, bundle manifest source/artifact
hashes, SBOM reference, provenance statement, OCI image digest, and runtime
version. Environment configuration and credentials may change; artifacts may
not.

Promotion gates are: source tests, deterministic rebuild comparison, SBOM and
provenance policy, injected signature verification when required, vulnerability
policy, preflight, migration review, rollout health, the API/worker/queue/storage/
connector smoke suite, and recorded human approval for production. A failure
creates a new candidate; no file is patched inside an existing archive.

The included `injected` signature envelope is an interface, not key management.
Release engineering owns signer isolation, verifier distribution, trust roots,
revocation, transparency/audit storage, and rotation.
