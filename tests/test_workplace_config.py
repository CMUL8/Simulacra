from __future__ import annotations

from simulacra.workplace.config import WORKPLACE_FLAGS, workplace_flags_for_tenant


def test_internal_tenant_allowlist_defaults_off(monkeypatch):
    monkeypatch.setenv("SIMULACRA_WORKPLACE_INTERNAL_TENANTS", "tenant_internal")
    flags = workplace_flags_for_tenant("tenant_internal")
    assert tuple(flags) == WORKPLACE_FLAGS
    assert flags == {name: False for name in WORKPLACE_FLAGS}
