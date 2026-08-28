"""Server-owned workplace capability flags.

The first workplace release deliberately ships every capability disabled.  The
resolver accepts the authenticated tenant only so later server-side policy can
scope a rollout without changing the public bootstrap contract.
"""

from __future__ import annotations


WORKPLACE_FLAGS: tuple[str, ...] = (
    "workplace_shell_v1",
    "workplace_attention_v1",
    "workplace_conversation_v1",
    "workplace_files_v1",
    "workplace_preview_origin_v1",
    "workplace_sse_v1",
    "workplace_bootstrap_v1",
)


def workplace_flags_for_tenant(tenant_id: str) -> dict[str, bool]:
    """Return only public booleans for an already-authenticated tenant.

    ``tenant_id`` intentionally has no client-visible policy effect in W1D:
    all flags remain off until the later server-controlled rollout wave.
    """
    del tenant_id
    return {name: False for name in WORKPLACE_FLAGS}
