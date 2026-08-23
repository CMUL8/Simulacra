# CMUL8 customer-managed infrastructure contracts

These modules are intentionally non-provisioning skeletons. They validate and
normalize references to infrastructure that a customer or platform team owns:
Kubernetes, PostgreSQL, Redis, object storage, secret management, DNS/TLS, and
an OCI registry. They do not claim production readiness and create no cloud
resources.

Choose exactly one module under `modules/`, provide resource identifiers and
private endpoints from your cloud landing zone, then pass its `runtime_contract`
output to the installation preflight. Provider authentication, networking,
backup policy, HA/DR, encryption keys, observability, budgets, and compliance
controls remain customer responsibilities.

The interfaces use strings rather than provider resources so `terraform
validate` requires no provider downloads.
