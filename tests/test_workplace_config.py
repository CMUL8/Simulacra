from __future__ import annotations

from simulacra.workplace.config import WORKPLACE_FLAGS, workplace_flags_for_tenant


def test_internal_tenant_allowlist_defaults_off(monkeypatch):
    monkeypatch.delenv("SIMULACRA_WORKPLACE_INTERNAL_TENANTS", raising=False)
    flags = workplace_flags_for_tenant("tenant_internal")
    assert tuple(flags) == WORKPLACE_FLAGS
    assert flags == {name: False for name in WORKPLACE_FLAGS}


def test_internal_tenant_allowlist_activates_exact_tenant_only(monkeypatch):
    monkeypatch.setenv(
        "SIMULACRA_WORKPLACE_INTERNAL_TENANTS",
        "tenant_internal,tenant_second",
    )

    assert workplace_flags_for_tenant("tenant_internal") == {
        name: True for name in WORKPLACE_FLAGS
    }
    assert workplace_flags_for_tenant("tenant_second") == {
        name: True for name in WORKPLACE_FLAGS
    }
    assert workplace_flags_for_tenant("tenant") == {
        name: False for name in WORKPLACE_FLAGS
    }
    assert workplace_flags_for_tenant("tenant_internal_extra") == {
        name: False for name in WORKPLACE_FLAGS
    }


def test_internal_tenant_allowlist_fails_closed_for_empty_whitespace_or_invalid_values(monkeypatch):
    for configured in (
        "",
        " ",
        "tenant_internal, tenant_second",
        "tenant_internal,",
        "tenant_internal,../escape",
        "TENANT_INTERNAL",
    ):
        monkeypatch.setenv("SIMULACRA_WORKPLACE_INTERNAL_TENANTS", configured)
        assert workplace_flags_for_tenant("tenant_internal") == {
            name: False for name in WORKPLACE_FLAGS
        }


def test_invalid_authenticated_tenant_never_activates_flags(monkeypatch):
    monkeypatch.setenv("SIMULACRA_WORKPLACE_INTERNAL_TENANTS", "tenant_internal")
    assert workplace_flags_for_tenant("../tenant_internal") == {
        name: False for name in WORKPLACE_FLAGS
    }
