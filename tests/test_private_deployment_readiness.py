from __future__ import annotations

import json

from deploy.readiness import (
    REQUIRED_PROBES,
    assess_private_deployment,
    render_readiness_report,
)


def private_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "CMUL8_DEPLOYMENT_MODE": "private_cloud",
        "CMUL8_TENANT_ID": "acme",
        "CMUL8_ENVIRONMENT": "production",
        "CMUL8_POSTGRES_URL": "postgresql://cmul8:super-secret@postgres:5432/cmul8",
        "CMUL8_REDIS_URL": "redis://:another-secret@redis:6379/0",
        "CMUL8_SECRET_PROVIDER": "vault",
        "CMUL8_TLS_REQUIRED": "true",
        "SIMULACRA_AUTH_REQUIRED": "1",
        "SIMULACRA_BOOTSTRAP_EMAIL": "owner@acme.test",
        "SIMULACRA_BOOTSTRAP_PASSWORD": "never-print-this",
        "CMUL8_BACKUP_REFERENCE": "backup/acme-2026-08-30",
        "CMUL8_RESTORE_TEST_REFERENCE": "restore/acme-2026-08-29",
    }
    environment.update(overrides)
    return environment


def passing_probes():
    return {name: (lambda: True) for name in REQUIRED_PROBES}


def test_private_readiness_produces_a_secret_safe_release_decision():
    report = assess_private_deployment(private_environment(), probes=passing_probes())

    assert report.startup_ready is True
    assert report.production_ready is True
    assert report.format == "missions.private-readiness.v1"
    assert [check.id for check in report.checks] == [
        "environment",
        "single_tenant",
        "authentication",
        "database",
        "queue",
        "storage",
        "executor",
        "recovery",
    ]
    serialized = json.dumps(report.to_dict(), sort_keys=True)
    assert "super-secret" not in serialized
    assert "another-secret" not in serialized
    assert "never-print-this" not in serialized
    assert report.to_dict()["deployment"] == {
        "mode": "private_cloud",
        "tenant": "acme",
        "environment": "production",
    }


def test_private_readiness_distinguishes_startup_from_production_recovery_gate():
    environment = private_environment()
    environment.pop("CMUL8_RESTORE_TEST_REFERENCE")

    report = assess_private_deployment(environment, probes=passing_probes())

    assert report.startup_ready is True
    assert report.production_ready is False
    recovery = next(check for check in report.checks if check.id == "recovery")
    assert recovery.status == "blocked"
    assert recovery.scope == "production"
    assert "restore drill" in recovery.message.lower()


def test_private_readiness_fails_closed_without_leaking_probe_exceptions():
    probes = passing_probes()

    def broken_database():
        raise RuntimeError("postgresql://admin:leaked-password@private-db/acme")

    probes["database"] = broken_database
    report = assess_private_deployment(private_environment(), probes=probes)

    assert report.startup_ready is False
    database = next(check for check in report.checks if check.id == "database")
    assert database.status == "blocked"
    assert database.message == "Database is not reachable."
    assert "leaked-password" not in json.dumps(report.to_dict())


def test_private_readiness_enforces_one_tenant_for_the_private_profile():
    report = assess_private_deployment(
        private_environment(SIMULACRA_WORKPLACE_INTERNAL_TENANTS="acme,other"),
        probes=passing_probes(),
    )

    assert report.startup_ready is False
    tenant = next(check for check in report.checks if check.id == "single_tenant")
    assert tenant.status == "blocked"
    assert tenant.message == "Private deployment must be scoped to exactly one tenant."


def test_private_readiness_keeps_tls_as_a_startup_requirement():
    report = assess_private_deployment(
        private_environment(CMUL8_TLS_REQUIRED="false"),
        probes=passing_probes(),
    )

    assert report.startup_ready is False
    environment = next(check for check in report.checks if check.id == "environment")
    assert environment.status == "blocked"


def test_human_report_leads_with_the_operator_decision_and_next_action():
    environment = private_environment()
    environment.pop("CMUL8_BACKUP_REFERENCE")
    report = assess_private_deployment(environment, probes=passing_probes())

    rendered = render_readiness_report(report)

    assert rendered.startswith("Missions private deployment: STARTUP READY, PRODUCTION BLOCKED")
    assert "Record a backup and successful restore drill before opening this deployment to humans." in rendered
    assert "CMUL8_POSTGRES_URL" not in rendered
