"""Explicit harness selection.  This module never falls back between adapters."""

from __future__ import annotations

from typing import Any

from .codex import CodexAppServerTransport, CodexHarness
from .contracts import HarnessConfig
from .fake import FakeHarness
from .prime import PrimeHarness


def create_harness(config: HarnessConfig | None = None, **adapters: Any):
    selected = config or HarnessConfig.from_env()
    if selected.harness == "codex":
        transport = adapters.get("codex_transport")
        if transport is None and adapters.get("use_default_transport", True):
            transport = CodexAppServerTransport()
        return CodexHarness(transport=transport, session_repository=adapters.get("session_repository"))
    if selected.harness == "prime":
        return PrimeHarness(runner=adapters.get("prime_runner"), session_repository=adapters.get("session_repository"))
    if selected.harness == "fake":
        return FakeHarness(session_repository=adapters.get("session_repository"))
    # HarnessConfig validates this already; retain an explicit guard for callers
    # supplying duck-typed config objects.
    raise ValueError(f"No adapter registered for {selected.harness!r}; no fallback is attempted")
