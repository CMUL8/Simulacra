from __future__ import annotations

from pathlib import Path

import yaml

from deploy.release import assess_upgrade

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"


def release_state(
    *, bundle: str, image: str = "1", schema: int = 1, compatible: bool = True, evidence: bool = False
) -> dict:
    state = {
        "bundle_hash": bundle * 64,
        "image_digest": "sha256:" + image * 64,
        "runtime_version": "0.1.0",
        "schema_version": schema,
        "migration_backward_compatible": compatible,
    }
    if evidence:
        state |= {
            "backup_reference": "backup/change-42",
            "restore_test_reference": "restore-drill/change-41",
        }
    return state


def test_upgrade_assessment_requires_immutable_artifacts_and_recovery_evidence():
    current = release_state(bundle="a", schema=1)
    target = release_state(bundle="b", image="2", schema=2, compatible=False)
    blocked = assess_upgrade(current, target)
    assert not blocked.ready
    assert not blocked.rollback_allowed
    assert blocked.errors == (
        "schema-changing upgrade requires a backup reference",
        "schema-changing upgrade requires tested restore evidence",
    )
    target.update(backup_reference="backup/change-42", restore_test_reference="restore/change-41")
    assessed = assess_upgrade(current, target)
    assert assessed.ready
    assert not assessed.rollback_allowed
    target["migration_backward_compatible"] = True
    assert assess_upgrade(current, target).rollback_allowed


def test_upgrade_assessment_rejects_tags_downgrades_and_same_bundle():
    current = release_state(bundle="a", schema=2)
    target = release_state(bundle="a", schema=1)
    target["image_digest"] = "latest"
    result = assess_upgrade(current, target)
    assert not result.ready
    assert "target.image_digest must be an immutable sha256 digest" in result.errors
    assert "target bundle must differ from current bundle" in result.errors
    assert "schema downgrade is unsupported" in result.errors


def test_compose_runtime_is_small_durable_and_health_checked():
    document = yaml.safe_load(COMPOSE.read_text())
    services = document["services"]
    assert set(services) == {"postgres", "redis", "migrate", "api", "worker"}
    assert services["api"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    assert services["worker"]["depends_on"]["migrate"]["condition"] == "service_completed_successfully"
    for service in ("postgres", "redis", "api", "worker"):
        assert "healthcheck" in services[service]
    for service in ("migrate", "api", "worker"):
        assert services[service]["read_only"] is True
        assert "ALL" in services[service]["cap_drop"]
        assert "no-new-privileges:true" in services[service]["security_opt"]
        mounts = services[service]["volumes"]
        assert "cmul8-data:/app/data" in mounts
        assert "cmul8-runs:/app/runs" in mounts
    assert services["api"]["ports"] == [
        "${CMUL8_BIND_ADDRESS:-127.0.0.1}:${CMUL8_PORT:-8000}:8000"
    ]


def test_compose_requires_operator_supplied_passwords_and_has_no_kubernetes_contract():
    text = COMPOSE.read_text()
    assert "${CMUL8_POSTGRES_PASSWORD:?set CMUL8_POSTGRES_PASSWORD}" in text
    assert "${SIMULACRA_BOOTSTRAP_PASSWORD:?set SIMULACRA_BOOTSTRAP_PASSWORD}" in text
    assert "kubernetes" not in text.lower()
    assert "helm" not in text.lower()
    assert "simulacra-admin-change-me" not in text
