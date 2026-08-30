"""Operator-facing readiness decision for a private Missions deployment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

from .environment import validate_environment

REQUIRED_PROBES = ("database", "queue", "storage", "executor")


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    status: str
    scope: str
    message: str
    action: str | None = None

    def to_dict(self) -> dict[str, str]:
        value = {
            "id": self.id,
            "status": self.status,
            "scope": self.scope,
            "message": self.message,
        }
        if self.action:
            value["action"] = self.action
        return value


@dataclass(frozen=True)
class PrivateReadinessReport:
    format: str
    mode: str
    tenant: str
    environment: str
    startup_ready: bool
    production_ready: bool
    checks: tuple[ReadinessCheck, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "format": self.format,
            "deployment": {
                "mode": self.mode,
                "tenant": self.tenant,
                "environment": self.environment,
            },
            "startup_ready": self.startup_ready,
            "production_ready": self.production_ready,
            "summary": {
                "passed": sum(check.status == "passed" for check in self.checks),
                "blocked": sum(check.status == "blocked" for check in self.checks),
            },
            "checks": [check.to_dict() for check in self.checks],
        }


def _passed(check_id: str, scope: str, message: str) -> ReadinessCheck:
    return ReadinessCheck(check_id, "passed", scope, message)


def _blocked(check_id: str, scope: str, message: str, action: str) -> ReadinessCheck:
    return ReadinessCheck(check_id, "blocked", scope, message, action)


def _probe_check(
    name: str,
    probes: Mapping[str, Callable[[], object]],
) -> ReadinessCheck:
    labels = {
        "database": ("Database is reachable.", "Database is not reachable.", "Check the private database endpoint and network policy."),
        "queue": ("Work queue is reachable.", "Work queue is not reachable.", "Check the private queue endpoint and network policy."),
        "storage": ("Mission storage is writable.", "Mission storage is not writable.", "Mount writable data and run volumes for the Missions service account."),
        "executor": ("Agent execution is available.", "Agent execution is unavailable.", "Select an executor certified and baked into this deployment image."),
    }
    success, failure, action = labels[name]
    probe = probes.get(name)
    if probe is None:
        return _blocked(name, "startup", failure, action)
    try:
        ok = bool(probe())
    except Exception:
        # Probe exceptions often contain credentials or internal endpoints. The
        # operator report deliberately exposes only a bounded remediation.
        ok = False
    return _passed(name, "startup", success) if ok else _blocked(name, "startup", failure, action)


def assess_private_deployment(
    environment: Mapping[str, str],
    *,
    probes: Mapping[str, Callable[[], object]],
) -> PrivateReadinessReport:
    """Return a stable, secret-safe startup and production decision.

    The report records identifiers and check outcomes, never configuration
    values, service URLs, credentials, exception text, or filesystem paths.
    """
    checks: list[ReadinessCheck] = []
    validated = validate_environment(environment)
    tls_required = environment.get("CMUL8_TLS_REQUIRED", "true").lower() in {"1", "true", "yes"}
    if validated.ok and tls_required:
        checks.append(_passed("environment", "startup", "Deployment settings are valid."))
    else:
        checks.append(_blocked(
            "environment",
            "startup",
            "Deployment settings need attention.",
            "Correct the required private-deployment settings and run this check again.",
        ))

    tenant = environment.get("CMUL8_TENANT_ID", "").strip()
    allowlist = [value for value in environment.get("SIMULACRA_WORKPLACE_INTERNAL_TENANTS", "").split(",") if value]
    single_tenant = (
        environment.get("CMUL8_DEPLOYMENT_MODE") == "private_cloud"
        and bool(tenant)
        and (not allowlist or allowlist == [tenant])
    )
    checks.append(
        _passed("single_tenant", "startup", "Deployment is scoped to one tenant.")
        if single_tenant
        else _blocked(
            "single_tenant",
            "startup",
            "Private deployment must be scoped to exactly one tenant.",
            "Use private_cloud mode and configure only the deployment tenant.",
        )
    )

    auth_required = environment.get("SIMULACRA_AUTH_REQUIRED", "").lower() in {"1", "true", "yes"}
    identity_ready = bool(environment.get("SIMULACRA_BOOTSTRAP_EMAIL", "").strip()) and bool(
        environment.get("SIMULACRA_BOOTSTRAP_PASSWORD", "").strip()
    )
    checks.append(
        _passed("authentication", "startup", "Human access requires authentication.")
        if auth_required and identity_ready
        else _blocked(
            "authentication",
            "startup",
            "Private deployment authentication is incomplete.",
            "Require authentication and provide the initial owner email and a strong bootstrap password.",
        )
    )

    checks.extend(_probe_check(name, probes) for name in REQUIRED_PROBES)

    recovery_ready = bool(environment.get("CMUL8_BACKUP_REFERENCE", "").strip()) and bool(
        environment.get("CMUL8_RESTORE_TEST_REFERENCE", "").strip()
    )
    checks.append(
        _passed("recovery", "production", "Backup and restore-drill evidence is recorded.")
        if recovery_ready
        else _blocked(
            "recovery",
            "production",
            "Backup or restore drill evidence is missing.",
            "Record a backup and successful restore drill before opening this deployment to humans.",
        )
    )

    startup_ready = all(check.status == "passed" for check in checks if check.scope == "startup")
    production_ready = startup_ready and all(check.status == "passed" for check in checks)
    return PrivateReadinessReport(
        format="missions.private-readiness.v1",
        mode=environment.get("CMUL8_DEPLOYMENT_MODE", "unset"),
        tenant=tenant or "unset",
        environment=environment.get("CMUL8_ENVIRONMENT", "unset"),
        startup_ready=startup_ready,
        production_ready=production_ready,
        checks=tuple(checks),
    )


def render_readiness_report(report: PrivateReadinessReport) -> str:
    if report.production_ready:
        decision = "READY FOR PRODUCTION"
    elif report.startup_ready:
        decision = "STARTUP READY, PRODUCTION BLOCKED"
    else:
        decision = "STARTUP BLOCKED"
    lines = [f"Missions private deployment: {decision}"]
    for check in report.checks:
        marker = "PASS" if check.status == "passed" else "FIX"
        lines.append(f"[{marker}] {check.message}")
        if check.status == "blocked" and check.action:
            lines.append(f"      Next: {check.action}")
    return "\n".join(lines)
