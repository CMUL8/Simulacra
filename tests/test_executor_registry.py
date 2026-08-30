from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulacra.missions import JsonProcessMissionAgentExecutor
from simulacra.missions.executor_registry import (
    ExecutorRegistryError,
    build_executor_factories,
    load_executor_registry,
    main,
)

ROOT = Path(__file__).parents[1]


def write_registry(path: Path, executors: list[dict[str, str]]) -> Path:
    path.write_text(json.dumps({
        "format": "missions.executor-registry.v1",
        "executors": executors,
    }), encoding="utf-8")
    path.chmod(0o644)
    return path


def json_process_entry(identifier: str = "prime") -> dict[str, str]:
    return {
        "id": identifier,
        "adapter": "json-process",
        "protocol": "mission-executor-json-v1",
        "network_policy": "enforced",
    }


def install_runtime(root: Path, identifier: str) -> Path:
    executable = root / identifier / "bin" / "mission-executor"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o555)
    return executable


def test_registry_turns_one_reviewed_entry_into_the_provider_neutral_adapter(tmp_path: Path):
    registry_path = write_registry(tmp_path / "registry.json", [
        {"id": "codex", "adapter": "builtin", "protocol": "codex-app-server-v1", "network_policy": "enforced"},
        json_process_entry("prime"),
    ])
    executable = install_runtime(tmp_path, "prime")

    registry = load_executor_registry(registry_path, verify_runtimes=True)
    factories = build_executor_factories(registry)

    assert factories["codex"]() is None
    executor = factories["prime"]()
    assert isinstance(executor, JsonProcessMissionAgentExecutor)
    assert executor.name == "prime"
    assert executor.executable_path() == executable


@pytest.mark.parametrize("entry", [
    json_process_entry("../escape"),
    json_process_entry("Prime"),
    json_process_entry("prime") | {"adapter": "python-import"},
    json_process_entry("prime") | {"protocol": "unreviewed-v2"},
    json_process_entry("prime") | {"network_policy": "open"},
    json_process_entry("prime") | {"executable": "/tmp/provider"},
])
def test_registry_rejects_unsafe_or_ambiguous_provider_entries(tmp_path: Path, entry: dict[str, str]):
    registry_path = write_registry(tmp_path / "registry.json", [entry])
    with pytest.raises(ExecutorRegistryError):
        load_executor_registry(registry_path)


def test_registry_requires_the_conventional_baked_runtime_when_verified(tmp_path: Path):
    registry_path = write_registry(tmp_path / "registry.json", [json_process_entry("hermes")])

    with pytest.raises(ExecutorRegistryError, match="runtime executable"):
        load_executor_registry(registry_path, verify_runtimes=True)


def test_registry_rejects_duplicate_ids_and_unknown_document_fields(tmp_path: Path):
    duplicated = write_registry(tmp_path / "registry.json", [json_process_entry(), json_process_entry()])
    with pytest.raises(ExecutorRegistryError, match="unique"):
        load_executor_registry(duplicated)
    duplicated.write_text(json.dumps({
        "format": "missions.executor-registry.v1",
        "executors": [json_process_entry()],
        "factory": "module:callable",
    }), encoding="utf-8")
    with pytest.raises(ExecutorRegistryError, match="fields"):
        load_executor_registry(duplicated)


def test_registry_validator_gives_a_clear_provider_facing_result(tmp_path: Path, capsys):
    registry_path = write_registry(tmp_path / "registry.json", [json_process_entry("prime")])
    install_runtime(tmp_path, "prime")

    assert main([str(registry_path), "--verify-runtimes", "--format", "human"]) == 0
    output = capsys.readouterr().out
    assert "Executor registry is valid" in output
    assert "prime" in output
    assert str(tmp_path) not in output


def test_base_image_bakes_only_the_source_controlled_default_registry():
    registry = load_executor_registry(ROOT / "deploy" / "executor-registry.json")
    assert [entry.id for entry in registry.entries] == ["codex"]
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY deploy/executor-registry.json /opt/cmul8/executors/registry.json" in dockerfile
