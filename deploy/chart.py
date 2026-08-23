"""Helm install/upgrade rendering boundary with injectable command execution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable, Iterable


class ChartValidationError(ValueError):
    pass


Runner = Callable[[list[str]], str]


def _subprocess_runner(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


def validate_rendered_manifest(rendered: str) -> None:
    if "{{" in rendered or "}}" in rendered:
        raise ChartValidationError("rendered manifest contains unresolved template expressions")
    required = ("kind: Deployment", "kind: Service", "kind: PodDisruptionBudget", "app.kubernetes.io/component: web", "app.kubernetes.io/component: api", "app.kubernetes.io/component: worker")
    missing = [value for value in required if value not in rendered]
    if missing:
        raise ChartValidationError(f"rendered manifest misses required resources: {missing}")
    if "kind: Secret" in rendered:
        raise ChartValidationError("chart must not render credentials")
    for field in ("readOnlyRootFilesystem: true", "allowPrivilegeEscalation: false", "runAsNonRoot: true", "livenessProbe:", "readinessProbe:"):
        if field not in rendered:
            raise ChartValidationError(f"rendered manifest misses security/health field: {field}")


def render_chart(
    chart: str | Path,
    *,
    release: str,
    namespace: str,
    values_files: Iterable[str | Path] = (),
    upgrade: bool = False,
    runner: Runner | None = None,
) -> str:
    """Run Helm lint/template without shell expansion, then validate output."""
    if runner is None:
        if shutil.which("helm") is None:
            raise ChartValidationError("helm binary is required for real chart rendering")
        runner = _subprocess_runner
    chart_path = str(Path(chart))
    values_args: list[str] = []
    for value in values_files:
        values_args.extend(("--values", str(Path(value))))
    runner(["helm", "lint", chart_path, *values_args])
    command = ["helm", "template", release, chart_path, "--namespace", namespace, *values_args]
    if upgrade:
        command.append("--is-upgrade")
    rendered = runner(command)
    validate_rendered_manifest(rendered)
    return rendered
