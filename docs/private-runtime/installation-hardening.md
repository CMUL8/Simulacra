# Installation hardening checklist

Before install, enforce namespace Pod Security `restricted`, admission policy
for digest-only images, an approved registry, signature policy when configured,
and quotas. Confirm the external Secret exists without printing it. Use a
dedicated service account with token automount disabled; workload identity
annotations must grant only required secret/storage access.

The chart defaults to non-root containers, dropped capabilities, RuntimeDefault
seccomp, a read-only root filesystem, bounded `/tmp`, resource requests/limits,
topology spreading, disruption budgets, disabled service links, and an ingress
policy. Set `networkPolicy.ingressNamespaceSelector` to the actual ingress
controller namespace. The policy intentionally does not restrict egress:
Postgres, Redis, object storage, DNS, secret providers, and configured
connectors are customer-specific. Apply a reviewed egress policy separately.

Worker probes invoke the contracted health binary. It must inspect the running
worker and queue-consumption readiness; a marker-file-only implementation is
not compliant. HTTP liveness must test process health only. Readiness may test
whether the pod can serve safely; dependency failure must not create restart
storms.

Use `helm upgrade --install --atomic --wait --timeout 15m`. Retain failed hook
Jobs for diagnosis. Confirm preflight completes before migrations, all three
Deployments reach Available, disruption budgets select the expected pods, and
all smoke checks pass before traffic promotion.
