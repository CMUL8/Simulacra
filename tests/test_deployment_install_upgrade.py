from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from deploy.chart import ChartValidationError, render_chart, validate_rendered_manifest
from deploy.release import assess_upgrade

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts" / "cmul8"
CI_VALUES = CHART / "ci" / "private-runtime-values.yaml"

RENDERED_CONTRACT = """
kind: Deployment
app.kubernetes.io/component: web
app.kubernetes.io/component: api
app.kubernetes.io/component: worker
kind: Service
kind: PodDisruptionBudget
readOnlyRootFilesystem: true
allowPrivilegeEscalation: false
runAsNonRoot: true
livenessProbe:
readinessProbe:
"""


def release_state(*, bundle: str, image: str = "1", schema: int = 1, compatible: bool = True, evidence: bool = False) -> dict:
    state = {
        "bundle_hash": bundle * 64,
        "image_digest": "sha256:" + image * 64,
        "chart_version": "0.1.0",
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


def test_install_and_upgrade_render_commands_are_distinct_and_validated():
    commands: list[list[str]] = []

    def runner(command: list[str]) -> str:
        commands.append(command)
        return RENDERED_CONTRACT if command[1] == "template" else "lint ok"

    install = render_chart(CHART, release="cmul8", namespace="runtime", values_files=[CI_VALUES], runner=runner)
    upgrade = render_chart(CHART, release="cmul8", namespace="runtime", values_files=[CI_VALUES], upgrade=True, runner=runner)
    assert install == upgrade == RENDERED_CONTRACT
    template_commands = [command for command in commands if command[1] == "template"]
    assert "--is-upgrade" not in template_commands[0]
    assert template_commands[1][-1] == "--is-upgrade"
    assert all("--values" in command for command in commands)


def test_render_validator_rejects_unresolved_templates_and_secrets():
    with pytest.raises(ChartValidationError, match="unresolved"):
        validate_rendered_manifest(RENDERED_CONTRACT + "{{ unresolved }}")
    with pytest.raises(ChartValidationError, match="credentials"):
        validate_rendered_manifest(RENDERED_CONTRACT + "\nkind: Secret\n")


def test_private_runtime_maps_database_and_durable_state_contracts():
    helpers = (CHART / "templates" / "_helpers.tpl").read_text()
    deployments = (CHART / "templates" / "deployments.yaml").read_text()
    pvc = (CHART / "templates" / "pvc.yaml").read_text()
    assert "SIMULACRA_DATABASE_URL" in helpers
    assert "SIMULACRA_DATA_DIR" in helpers and "SIMULACRA_RUNS_DIR" in helpers
    assert "mountPath: /var/lib/cmul8" in deployments
    assert "persistentVolumeClaim" in deployments
    assert "kind: PersistentVolumeClaim" in pvc


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm binary unavailable; CI must run this real render")
@pytest.mark.parametrize("upgrade", [False, True], ids=["install", "upgrade"])
def test_real_helm_install_and_upgrade_render(upgrade: bool):
    rendered = render_chart(
        CHART,
        release="cmul8-ci",
        namespace="cmul8-ci",
        values_files=[CI_VALUES],
        upgrade=upgrade,
    )
    assert "kind: Deployment" in rendered
