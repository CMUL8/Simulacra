"""Server-owned workplace capability flags.

Customers never choose these values.  Release operators may activate the
workplace for an exact, validated internal-tenant allowlist while every other
tenant remains on the legacy product.
"""

from __future__ import annotations

import os
import re


WORKPLACE_FLAGS: tuple[str, ...] = (
    "workplace_shell_v1",
    "workplace_attention_v1",
    "workplace_conversation_v1",
    "workplace_files_v1",
    "workplace_preview_origin_v1",
    "workplace_sse_v1",
    "workplace_bootstrap_v1",
)
WORKPLACE_INTERNAL_TENANTS_ENV = "SIMULACRA_WORKPLACE_INTERNAL_TENANTS"
_TENANT_ID = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


def _internal_tenant_allowlist(raw: str | None) -> frozenset[str]:
    """Parse the operator-owned exact tenant allowlist, failing closed.

    Empty configuration means the rollout is disabled.  Whitespace, empty
    members, duplicates, or invalid identifiers invalidate the complete value
    rather than partially enabling a tenant after an operator typo.
    """
    if raw is None or raw == "":
        return frozenset()
    if raw != raw.strip() or any(character.isspace() for character in raw):
        return frozenset()
    tenant_ids = raw.split(",")
    if (
        any(not _TENANT_ID.fullmatch(tenant_id) for tenant_id in tenant_ids)
        or len(set(tenant_ids)) != len(tenant_ids)
    ):
        return frozenset()
    return frozenset(tenant_ids)


def workplace_flags_for_tenant(tenant_id: str) -> dict[str, bool]:
    """Return public booleans for an already-authenticated tenant.

    The authenticated server-side tenant is matched exactly.  Invalid caller
    scope or invalid operator configuration keeps every capability disabled.
    """
    enabled = bool(_TENANT_ID.fullmatch(tenant_id)) and tenant_id in _internal_tenant_allowlist(
        os.environ.get(WORKPLACE_INTERNAL_TENANTS_ENV)
    )
    return {name: enabled for name in WORKPLACE_FLAGS}
