from pathlib import Path


ROOT = Path(__file__).parents[1]
INTERFACE = ROOT / "docs" / "MISSION_EXECUTOR_PROVIDER_INTERFACE.md"


def test_executor_provider_interface_covers_the_frozen_runtime_contract() -> None:
    text = INTERFACE.read_text(encoding="utf-8")
    required_contracts = (
        "mission-executor-json-v1",
        "/opt/cmul8/executors/<backend>/bin/mission-executor",
        '"schema_version": 1',
        '"type":"action_request"',
        '"type":"action_admitted"',
        '"type": "result"',
        "usage.steps",
        "2 MiB",
        "enforces_network_policy = True",
        "_CERTIFIED_EXECUTION_BACKENDS",
        "CMUL8_EXECUTION_BACKEND=enterprise",
        "test_generic_executor_reaches_the_real_launcher_sandbox_edge",
    )
    for contract in required_contracts:
        assert contract in text


def test_repository_readme_links_the_executor_provider_interface() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "[Missions executor provider interface](docs/MISSION_EXECUTOR_PROVIDER_INTERFACE.md)" in readme
