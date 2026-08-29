from __future__ import annotations

import json
from pathlib import Path

import pytest

from deploy.environment import validate_environment
from simulacra.workplace.config import WORKPLACE_FLAGS, workplace_flags_for_tenant


ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_ENV = "SIMULACRA_WORKPLACE_INTERNAL_TENANTS"


def _production_environment(**updates: str) -> dict[str, str]:
    environment = {
        "CMUL8_DEPLOYMENT_MODE": "cmul8_cloud",
        "CMUL8_TENANT_ID": "default",
        "CMUL8_ENVIRONMENT": "production",
        "CMUL8_POSTGRES_URL": "postgresql://db.internal/runtime",
        "CMUL8_REDIS_URL": "rediss://queue.internal/0",
    }
    environment.update(updates)
    return environment


def test_rollout_environment_allows_internal_tenant_only(monkeypatch):
    monkeypatch.setenv(ALLOWLIST_ENV, "tenant_internal")

    assert workplace_flags_for_tenant("tenant_internal") == {
        name: True for name in WORKPLACE_FLAGS
    }
    assert workplace_flags_for_tenant("tenant_customer") == {
        name: False for name in WORKPLACE_FLAGS
    }


@pytest.mark.parametrize(
    "configured",
    (
        " ",
        "tenant_internal, tenant_second",
        "tenant_internal,",
        "tenant_internal,../escape",
        "TENANT_INTERNAL",
    ),
)
def test_rollout_environment_rejects_malformed_internal_tenant_allowlist(configured: str):
    result = validate_environment(
        _production_environment(**{ALLOWLIST_ENV: configured})
    )

    assert not result.ok
    assert any(ALLOWLIST_ENV in error for error in result.errors)


def test_rollout_environment_contract_documents_validated_internal_allowlist():
    contract = json.loads((ROOT / "deploy" / "environment-contract.json").read_text())
    allowlist = contract["properties"][ALLOWLIST_ENV]

    assert allowlist["type"] == "string"
    assert allowlist["default"] == ""
    assert allowlist["description"]
    assert allowlist["pattern"]
    assert validate_environment(
        _production_environment(**{ALLOWLIST_ENV: "tenant_internal,tenant_second"})
    ).ok


def test_empty_rollout_allowlist_keeps_every_tenant_off(monkeypatch):
    monkeypatch.setenv(ALLOWLIST_ENV, "")
    assert validate_environment(_production_environment(**{ALLOWLIST_ENV: ""})).ok
    assert workplace_flags_for_tenant("default") == {
        name: False for name in WORKPLACE_FLAGS
    }
