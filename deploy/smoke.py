"""Injectable post-install smoke checks without network assumptions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

REQUIRED_CHECKS = ("api", "worker", "queue", "storage", "connectors")


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def run_smoke_checks(checks: Mapping[str, Callable[[], object]]) -> tuple[CheckResult, ...]:
    """Run every required check and retain all failures for actionable output."""
    missing = sorted(set(REQUIRED_CHECKS) - checks.keys())
    extra = sorted(set(checks) - set(REQUIRED_CHECKS))
    if missing or extra:
        raise ValueError(f"checks must match contract; missing={missing}, extra={extra}")
    results: list[CheckResult] = []
    for name in REQUIRED_CHECKS:
        try:
            response = checks[name]()
            if isinstance(response, tuple) and len(response) == 2:
                ok, detail = bool(response[0]), str(response[1])
            else:
                ok, detail = bool(response), "ok" if response else "check returned false"
        except Exception as exc:  # checks are an isolation boundary
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        results.append(CheckResult(name, ok, detail))
    return tuple(results)
