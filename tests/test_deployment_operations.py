from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from deploy.bundle import BundleError
from deploy.environment import REQUIRED, validate_environment
from deploy.release import create_rollback_manifest, create_upgrade_manifest
from deploy.smoke import REQUIRED_CHECKS, run_smoke_checks
from deploy.support import create_support_bundle

ROOT = Path(__file__).resolve().parents[1]


def valid_environment() -> dict[str, str]:
    return {
        "CMUL8_DEPLOYMENT_MODE": "private_cloud",
        "CMUL8_TENANT_ID": "tenant-one",
        "CMUL8_ENVIRONMENT": "production",
        "CMUL8_POSTGRES_URL": "postgresql://db.internal/runtime",
        "CMUL8_REDIS_URL": "rediss://queue.internal/0",
        "CMUL8_OBJECT_STORAGE_URL": "s3://runtime-bucket/prefix",
        "CMUL8_SECRET_PROVIDER": "vault",
        "CMUL8_IMAGE_REGISTRY": "registry.internal/cmul8",
    }


def test_preflight_accepts_contract_and_reports_all_failures():
    assert validate_environment(valid_environment()).ok
    broken = valid_environment()
    broken.pop("CMUL8_REDIS_URL")
    broken["CMUL8_TENANT_ID"] = "../escape"
    broken["CMUL8_OBJECT_STORAGE_URL"] = "https://user:password@objects.invalid/bucket"
    result = validate_environment(broken)
    assert not result.ok
    assert len(result.errors) == 3
    assert set(result.checked) == set(REQUIRED)


@pytest.mark.parametrize("preview_origin", [
    "https://preview.example.test/not-an-origin",
    "https://preview.example.test:invalid-port",
    "https://human@preview.example.test",
])
def test_preflight_fails_closed_for_malformed_preview_origins(preview_origin: str):
    environment = valid_environment() | {
        "CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1": "true",
        "CONTROL_ORIGIN": "https://app.example.test",
        "PREVIEW_ORIGIN": preview_origin,
        "PREVIEW_REGISTRABLE_DOMAIN": "example.test",
        "CMUL8_PREVIEW_EXCHANGE_SECRET": "test-secret",
    }
    result = validate_environment(environment)
    assert not result.ok
    assert any("preview origin" in error for error in result.errors)


def test_smoke_checks_are_injected_ordered_and_do_not_short_circuit():
    observed: list[str] = []
    checks = {}
    for name in REQUIRED_CHECKS:
        def check(name=name):
            observed.append(name)
            if name == "queue":
                raise RuntimeError("unavailable")
            return True, f"{name} reachable"
        checks[name] = check
    results = run_smoke_checks(checks)
    assert observed == list(REQUIRED_CHECKS)
    assert [item.name for item in results if not item.ok] == ["queue"]
    assert "RuntimeError" in results[2].detail


def test_support_bundle_is_deterministic_and_redacted(tmp_path: Path):
    environment = valid_environment() | {"CMUL8_ADMIN_TOKEN": "do-not-leak"}
    fixture = ROOT / "tests" / "fixtures" / "deployment"
    diagnostics = {
        "health.log": (fixture / "support-diagnostics.log").read_bytes(),
        "runtime.json": (fixture / "support-diagnostics.json").read_bytes(),
    }
    first = create_support_bundle(tmp_path / "one", environment=environment, diagnostics=diagnostics)
    second = create_support_bundle(tmp_path / "two", environment=environment, diagnostics=diagnostics)
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(first) as archive:
        content = b"\n".join(archive.extractfile(member).read() for member in archive if member.isfile())
    for forbidden in (
        b"hunter2",
        b"do-not-leak",
        b"bearer-token-value",
        b"prefixed-bearer-token",
        b"nested-bearer-token",
        b"access-token-value",
        b"access-token-text-value",
        b"client-secret-value",
        b"client-secret-text-value",
        b"api-key-value",
        b"api-key-text-value",
        b"refresh-token-quoted-value",
    ):
        assert forbidden not in content
    assert b"healthy" in content
    assert b"retained" in content
    assert content.count(b"[REDACTED]") >= 10
    with pytest.raises(BundleError, match="filename"):
        create_support_bundle(tmp_path, environment=environment, diagnostics={"private-key.pem": "key"})


@pytest.mark.parametrize("binary", [b"text\x00tail", b"\xff\xfe"])
def test_support_bundle_rejects_binary_diagnostics(tmp_path: Path, binary: bytes):
    with pytest.raises(BundleError, match="binary|UTF-8"):
        create_support_bundle(tmp_path, environment=valid_environment(), diagnostics={"health.log": binary})


def test_upgrade_and_rollback_records_are_explicit_and_deterministic():
    current, target = "a" * 64, "b" * 64
    assert create_upgrade_manifest(current, target) == create_upgrade_manifest(current, target)
    rollback = create_rollback_manifest(target, current, migration_compatible=False)
    assert rollback == {
        "format": "cmul8.rollback.v1",
        "from_bundle": target,
        "to_bundle": current,
        "migration_compatible": False,
        "requires_operator_approval": True,
        "phases": ["verify-target", "scale-workers", "rollout", "smoke", "record"],
    }


def test_runtime_process_contract_matches_compose_entrypoints():
	processes = json.loads((ROOT / "deploy" / "processes.json").read_text())
	compose = (ROOT / "docker-compose.yml").read_text()
	for process in ("api", "worker", "migrations"):
		assert f'command: ["{process}"]' in compose
	assert processes["image"]["writableMounts"] == ["/tmp", "/app/data", "/app/runs"]
	assert "cmul8-worker-health" in compose
	assert "no-new-privileges:true" in compose


def test_terraform_modules_are_honest_customer_managed_contracts():
    root = ROOT / "infra" / "terraform" / "modules"
    for cloud in ("aws", "azure", "gcp"):
        content = "\n".join(path.read_text() for path in (root / cloud).glob("*.tf"))
        assert 'customer_managed       = true' in content
        assert 'output "runtime_contract"' in content
        assert 'output "recovery_assessment"' in content
        assert 'output "network_assessment"' in content
        assert "tested_restore_reference" in content
        assert "attestation_only          = true" in content
        assert "postgres_endpoint" in content and "redis_endpoint" in content
        assert 'resource "' not in content
