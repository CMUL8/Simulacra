"""Deterministic contract coverage for the provider-neutral harness foundation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from simulacra.harnesses import (
    AgentRunRequest,
    CodexHarness,
    FakeHarness,
    HarnessConfig,
    ModelCapability,
    NetworkPolicy,
    PrimeHarness,
    ProviderConfig,
    TaskType,
    TerminalStatus,
    create_harness,
)


def _config(name: str = "fake") -> HarnessConfig:
    return HarnessConfig(name, ProviderConfig("custom", credential_env_var="CMUL8_TEST_TOKEN"), ModelCapability("test"))


def _request(tmp_path: Path, **overrides: object) -> AgentRunRequest:
    values: dict[str, object] = {
        "project_id": "proj_1", "environment_id": "env_1", "workspace": tmp_path,
        "prompt": "make a durable artifact", "role": "builder", "task_type": TaskType.BUILD_APP,
        "write_paths": (tmp_path,), "config": _config(),
    }
    values.update(overrides)
    return AgentRunRequest(**values)  # type: ignore[arg-type]


def test_default_selection_is_codex_and_no_adapter_fallback() -> None:
    assert HarnessConfig.from_env({}).harness == "codex"
    assert isinstance(create_harness(HarnessConfig.from_env({})), CodexHarness)
    assert isinstance(create_harness(_config("prime"), prime_runner=lambda _: {"response": "ok"}), PrimeHarness)
    assert {item.value for item in TaskType} == {
        "chat", "architect", "build_app", "build_workflow", "configure_agent", "qa", "research", "iterate", "repair",
    }
    for name in ("openai", "ollama", "lmstudio", "custom"):
        assert ProviderConfig(name).provider == name
    with pytest.raises(ValueError):
        ProviderConfig("unsupported")


def test_canonical_environment_wins_and_safe_metadata_never_resolves_secrets() -> None:
    config = HarnessConfig.from_env({
        "CMUL8_AGENT_HARNESS": "codex",
        "CMUL8_MODEL_PROVIDER": "custom",
        "CMUL8_MODEL": "canonical-model",
        "CMUL8_MODEL_BASE_URL": "https://model.example/v1",
        "CMUL8_MODEL_API_KEY_ENV": "CMUL8_CANONICAL_SECRET",
        "CMUL8_MODEL_REASONING_EFFORT": "high",
        "CMUL8_CODEX_PROFILE": "production",
        # Old aliases deliberately conflict; canonical names must win.
        "CMUL8_AGENT_PROVIDER": "ollama",
        "CMUL8_AGENT_MODEL": "legacy-model",
        "CMUL8_AGENT_CREDENTIAL_ENV": "CMUL8_LEGACY_SECRET",
        "CMUL8_CANONICAL_SECRET": "do-not-serialize-me",
    })
    assert config.provider.provider == "custom"
    assert config.provider.endpoint == "https://model.example/v1"
    assert config.provider.credential_env_var == "CMUL8_CANONICAL_SECRET"
    assert config.model.model_id == "canonical-model"
    assert config.model_reasoning_effort == "high"
    assert config.codex_profile == "production"
    assert "do-not-serialize-me" not in json.dumps(config.metadata())


def test_capability_registry_fields_are_typed_and_aliases_remain_coherent() -> None:
    capability = ModelCapability(
        "registry", tool_calling=True, structured_outputs=True, file_editing=True,
        patch_reliability=0.9, streaming=True, context_window=200_000,
        reasoning_controls=True, image_input=True, responses_api_compatible=True,
        approved_task_types={"chat", TaskType.BUILD_APP},
    )
    assert capability.structured_output and capability.source_edit
    assert capability.approved_task_types == frozenset({TaskType.CHAT, TaskType.BUILD_APP})


@pytest.mark.asyncio
async def test_codex_unavailable_is_honest_not_a_fake_live_sdk(tmp_path: Path) -> None:
    config = HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("codex"))
    harness = CodexHarness()
    health = await harness.healthcheck()
    result = await harness.run(_request(tmp_path, config=config))
    assert health["status"] == "unavailable"
    assert result.status is TerminalStatus.FAILED
    assert result.error and result.error["code"] == "codex_transport_unavailable"
    assert result.metadata["fallback"] == "none"


@pytest.mark.asyncio
async def test_fake_session_resume_events_and_durable_artifact(tmp_path: Path) -> None:
    harness = FakeHarness()
    request = _request(tmp_path)
    first = await harness.create_session(request)
    result = await harness.run(request)
    resumed = await harness.resume_session(request)
    events = [event async for event in harness.stream_events(result.session_id)]
    assert result.status is TerminalStatus.SUCCEEDED
    assert result.changed_files[0].is_file()
    assert resumed.session_id == first.session_id and resumed.resumed
    assert [event.action for event in events][0] == "run_started"
    assert result.events[-1].action == "run_finished"
    persisted = json.loads((tmp_path / ".cmul8/harness/sessions.json").read_text())
    assert "CMUL8_TEST_TOKEN" not in json.dumps(persisted)


@pytest.mark.asyncio
async def test_fake_error_timeout_cancellation_budget_and_no_artifact(tmp_path: Path) -> None:
    harness = FakeHarness()
    assert (await harness.run(_request(tmp_path, metadata={"fake": {"mode": "error"}}))).status is TerminalStatus.FAILED
    assert (await harness.run(_request(tmp_path, metadata={"fake": {"mode": "no_artifact"}}))).error["code"] == "artifact_validation"  # type: ignore[index]
    assert (await harness.run(_request(tmp_path, metadata={"fake": {"steps": 2}}, step_budget=1))).error["code"] == "step_budget_exceeded"  # type: ignore[index]
    timeout = await harness.run(_request(tmp_path, wall_timeout_seconds=0.01, metadata={"fake": {"mode": "timeout"}}))
    assert timeout.status is TerminalStatus.TIMED_OUT
    request = _request(tmp_path, metadata={"fake": {"delay_seconds": 0.05}})
    session = await harness.create_session(request)
    task = asyncio.create_task(harness.run(request))
    await asyncio.sleep(0)
    assert await harness.cancel(session.session_id)
    assert (await task).status is TerminalStatus.CANCELLED
    resumed = await harness.resume_session(request)
    assert resumed.session_id == session.session_id
    assert (await harness.run(request)).status is TerminalStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_policy_containment_network_and_runtime_agent_write_denial(tmp_path: Path) -> None:
    harness = FakeHarness()
    escaped = _request(tmp_path, write_paths=(tmp_path.parent,))
    result = await harness.run(escaped)
    assert result.error and result.error["code"] == "policy_denied"
    runtime = _request(tmp_path, metadata={"actor_type": "runtime_agent"})
    result = await harness.run(runtime)
    assert result.error and "runtime agents" in result.error["message"]
    allowed = _request(tmp_path, network_policy=NetworkPolicy.DECLARE_ONLY)
    assert (await harness.run(allowed)).status is TerminalStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_changed_file_path_escape_is_rejected(tmp_path: Path) -> None:
    escaped = await FakeHarness().run(_request(tmp_path, metadata={"fake": {"changed_files": ("../outside.txt",)}}))
    assert escaped.status is TerminalStatus.FAILED
    assert not (tmp_path.parent / "outside.txt").exists()

    class EscapeFake(FakeHarness):
        async def _run_provider(self, request, session):
            return {"changed_files": [tmp_path.parent / "outside.txt"], "steps": 1}

    result = await EscapeFake().run(_request(tmp_path))
    assert result.status is TerminalStatus.FAILED
    assert result.error and result.error["code"] == "artifact_validation"
