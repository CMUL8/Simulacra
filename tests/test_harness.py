"""Deterministic contract coverage for the provider-neutral harness foundation."""

from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import shutil
import signal
from types import SimpleNamespace
from pathlib import Path

import pytest

from simulacra.harnesses import (
    AgentRunRequest,
    AgentSession,
    CodexAppServerTransport,
    CodexHarness,
    CodexIsolationSpec,
    FakeHarness,
    HarnessConfig,
    JsonSessionRepository,
    ModelCapability,
    NetworkPolicy,
    PrimeHarness,
    ProviderConfig,
    TaskType,
    TerminalStatus,
    create_harness,
)
import simulacra.harnesses.codex as codex_module
from simulacra.harnesses.codex_provider import (
    CUSTOM_CREDENTIAL_ENV, OPENAI_BASE_URL, CodexProviderRoute, mission_app_server_args,
)


def _concurrent_session_save(workspace: str, role: str) -> None:
    repository = JsonSessionRepository(Path(workspace))
    repository.save(AgentSession(
        session_id=f"session-{role}", project_id="proj", role=role, harness="fake", provider="custom",
        model_id="test", environment_id="env", thread_id=f"thread-{role}",
    ))


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


def _isolated_request(tmp_path: Path, **overrides: object) -> AgentRunRequest:
    (tmp_path / "input-a").mkdir(exist_ok=True)
    (tmp_path / "input-b").mkdir(exist_ok=True)
    (tmp_path / "output").mkdir(exist_ok=True)
    metadata = {"mission_id": "mission_1", "run_id": "run_1", "agent_id": "agent_1"}
    metadata.update(overrides.pop("metadata", {}))  # type: ignore[arg-type]
    values: dict[str, object] = {
        "config": HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("codex-test")),
        "read_paths": (tmp_path / "input-b", tmp_path / "input-a"),
        "write_paths": (tmp_path / "output",),
        "network_policy": NetworkPolicy.DENY,
        "metadata": metadata,
    }
    values.update(overrides)
    return _request(tmp_path, **values)


def _isolation_spec(tmp_path: Path, request: AgentRunRequest) -> tuple[CodexIsolationSpec, Path]:
    launcher = tmp_path / "mission-sandbox"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o555)
    workspace = request.workspace.resolve(strict=False)
    read_roots = sorted(str(path.resolve(strict=False)) for path in request.read_paths)
    write_roots = sorted(str(path.resolve(strict=False)) for path in request.write_paths)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "workspace": str(workspace), "read_roots": read_roots, "write_roots": write_roots,
        "network": False, "mission_id": request.metadata["mission_id"],
        "run_id": request.metadata["run_id"], "agent_id": request.metadata["agent_id"],
    }, sort_keys=True), encoding="utf-8")
    manifest.chmod(0o600)
    return CodexIsolationSpec.from_files(launcher=launcher, manifest=manifest, allow_test_launcher=True), manifest


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


def test_operator_model_routes_are_exact_and_never_render_secret_values(tmp_path: Path) -> None:
    openai = CodexProviderRoute.from_config(HarnessConfig.from_env({}))
    assert openai.to_manifest() == {
        "provider": "openai", "endpoint": OPENAI_BASE_URL, "credential_env_var": "OPENAI_API_KEY",
    }
    custom_config = HarnessConfig(
        "codex", ProviderConfig("custom", endpoint="https://models.example/v1", credential_env_var=CUSTOM_CREDENTIAL_ENV),
        ModelCapability("gpt-oss-120b"),
    )
    custom = CodexProviderRoute.from_config(custom_config)
    argv = mission_app_server_args(tmp_path, custom)
    rendered = " ".join(argv)
    assert 'model_provider="cmul8_open"' in rendered
    assert "https://models.example/v1" in rendered and CUSTOM_CREDENTIAL_ENV in rendered
    assert "actual-provider-secret" not in rendered
    assert custom.allowed_environment_names() == {CUSTOM_CREDENTIAL_ENV}
    for endpoint in ("http://models.example/v1", "https://user:pass@models.example/v1", "https://models.example/v1?key=x"):
        with pytest.raises(ValueError):
            CodexProviderRoute("custom", endpoint, None)
    with pytest.raises(ValueError):
        CodexProviderRoute("custom", "https://models.example/v1", "AWS_SECRET_ACCESS_KEY")


def test_active_codex_process_group_registry_signals_only_registered_groups(monkeypatch):
    calls = []
    with codex_module._ACTIVE_CODEX_GROUPS_LOCK:
        codex_module._ACTIVE_CODEX_GROUPS.clear(); codex_module._ACTIVE_CODEX_GROUPS.update({11, 22})
    monkeypatch.setattr(os, "getpgrp", lambda: 11)
    monkeypatch.setattr(os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    codex_module.signal_active_codex_process_groups(signal.SIGTERM)
    assert calls == [(22, signal.SIGTERM)]
    with codex_module._ACTIVE_CODEX_GROUPS_LOCK: codex_module._ACTIVE_CODEX_GROUPS.clear()


@pytest.mark.asyncio
async def test_codex_disables_all_loaded_skills_and_fails_closed(tmp_path: Path, monkeypatch):
    request = _request(tmp_path, config=HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("x")))
    transport = CodexAppServerTransport(executable="codex")
    calls: list[tuple[str, dict]] = []
    async def rpc(method, params):
        calls.append((method, dict(params)))
        if method == "skills/list":
            if sum(1 for name, _ in calls if name == "skills/list") == 1:
                return {"data": [{"cwd": str(tmp_path), "errors": [], "skills": [{"enabled": True, "path": "/opt/codex/skills/demo/SKILL.md"}]}]}, []
            return {"data": [{"cwd": str(tmp_path), "errors": [], "skills": [{"enabled": False, "path": "/opt/codex/skills/demo/SKILL.md"}]}]}, []
        assert method == "skills/config/write" and params == {"path": "/opt/codex/skills/demo/SKILL.md", "enabled": False}
        return {"effectiveEnabled": False}, []
    monkeypatch.setattr(transport, "_rpc", rpc)
    await transport._disable_loaded_skills(request)
    assert [name for name, _ in calls] == ["skills/list", "skills/config/write", "skills/list"]

    async def still_enabled(method, _params):
        if method == "skills/list":
            return {"data": [{"cwd": str(tmp_path), "errors": [], "skills": [{"enabled": True, "path": "/x/SKILL.md"}]}]}, []
        return {"effectiveEnabled": True}, []
    monkeypatch.setattr(transport, "_rpc", still_enabled)
    with pytest.raises(RuntimeError, match="disable failed"):
        await transport._disable_loaded_skills(request)


@pytest.mark.asyncio
async def test_codex_wall_budget_covers_session_startup(tmp_path: Path, monkeypatch):
    request = _request(tmp_path, config=HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("x")), wall_timeout_seconds=0.01)
    transport = CodexAppServerTransport(executable="codex")
    async def hanging_start(_request):
        await asyncio.sleep(10)
    monkeypatch.setattr(transport, "_start", hanging_start)
    with pytest.raises(TimeoutError, match="wall timeout"):
        await transport.create_thread(request=request)


@pytest.mark.asyncio
async def test_codex_close_kills_descendants_after_leader_exited(monkeypatch):
    signals: list[int] = []; alive = {"value": True}; clock = {"value": -1.0}
    class Process:
        pid = 9876; returncode = 0; stdin = None
        async def wait(self): return 0
    def killpg(_pid, sig):
        if sig == 0:
            if not alive["value"]: raise ProcessLookupError
        else:
            signals.append(sig)
            if sig == signal.SIGKILL: alive["value"] = False
    async def nap(_seconds): return None
    def monotonic(): clock["value"] += 1; return clock["value"]
    monkeypatch.setattr(os, "killpg", killpg); monkeypatch.setattr(codex_module.time, "monotonic", monotonic); monkeypatch.setattr(asyncio, "sleep", nap)
    transport = CodexAppServerTransport(executable="codex"); transport._process = Process()  # type: ignore[assignment]
    with codex_module._ACTIVE_CODEX_GROUPS_LOCK: codex_module._ACTIVE_CODEX_GROUPS.add(9876)
    await transport.close()
    assert signals == [signal.SIGTERM, signal.SIGKILL]
    with codex_module._ACTIVE_CODEX_GROUPS_LOCK: assert 9876 not in codex_module._ACTIVE_CODEX_GROUPS


@pytest.mark.asyncio
async def test_codex_config_rejects_project_origin_and_provider_override(tmp_path: Path, monkeypatch):
    request = _request(tmp_path, config=HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("x")))
    transport = CodexAppServerTransport(executable="codex")
    async def project_config(_method, _params):
        base = {"projects": {str(tmp_path.resolve()): {"trust_level": "untrusted"}}, "model_provider": "openai", "openai_base_url": "https://api.openai.com/v1", "model_providers": {}, "mcp_servers": {}}
        return {"config": base, "origins": {"model": {"name": {"type": "project"}}}, "layers": [{"name": {"type": "sessionFlags"}, "config": base}]}, []
    monkeypatch.setattr(transport, "_rpc", project_config)
    with pytest.raises(RuntimeError, match="project config"):
        await transport._verify_mission_config(request)
    async def redirected(_method, _params):
        base = {"projects": {str(tmp_path.resolve()): {"trust_level": "untrusted"}}, "model_provider": "custom", "openai_base_url": "https://evil.invalid", "model_providers": {}, "mcp_servers": {}}
        return {"config": base, "origins": {}, "layers": [{"name": {"type": "sessionFlags"}, "config": base}]}, []
    monkeypatch.setattr(transport, "_rpc", redirected)
    with pytest.raises(RuntimeError, match="provider isolation"):
        await transport._verify_mission_config(request)


@pytest.mark.asyncio
async def test_codex_custom_responses_provider_is_verified_from_session_flags(tmp_path: Path, monkeypatch):
    endpoint = "https://models.example/v1"
    request = _request(tmp_path, config=HarnessConfig(
        "codex", ProviderConfig("custom", endpoint=endpoint, credential_env_var=CUSTOM_CREDENTIAL_ENV),
        ModelCapability("gpt-oss-120b"),
    ))
    transport = CodexAppServerTransport(executable="codex")
    workspace = str(tmp_path.resolve())
    minimal = {
        "cmul8_open": {
            "name": "CMUL8 Open Models", "base_url": endpoint, "env_key": CUSTOM_CREDENTIAL_ENV,
            "wire_api": "responses", "requires_openai_auth": False,
        },
    }
    provider_row = {
        "name": "CMUL8 Open Models", "base_url": endpoint, "env_key": CUSTOM_CREDENTIAL_ENV,
        "env_key_instructions": None, "experimental_bearer_token": None, "auth": None, "aws": None,
        "wire_api": "responses", "query_params": None, "http_headers": None, "env_http_headers": None,
        "request_max_retries": None, "stream_max_retries": None, "stream_idle_timeout_ms": None,
        "websocket_connect_timeout_ms": None, "requires_openai_auth": False, "supports_websockets": False,
        "supports_standalone_web_search": False,
    }
    session_config = {
        "projects": {workspace: {"trust_level": "untrusted"}}, "model_provider": "cmul8_open",
        "openai_base_url": OPENAI_BASE_URL, "model_providers": minimal,
    }
    effective = {**session_config, "model_providers": {"cmul8_open": provider_row}, "mcp_servers": {}}
    origin_keys = [
        "model_provider", "openai_base_url", "model_providers.cmul8_open.name",
        "model_providers.cmul8_open.base_url", "model_providers.cmul8_open.env_key",
        "model_providers.cmul8_open.wire_api", "model_providers.cmul8_open.requires_openai_auth",
    ]
    origins = {key: {"name": {"type": "sessionFlags"}} for key in origin_keys}

    async def rpc(method, _params):
        if method == "config/read":
            return {"config": effective, "origins": origins, "layers": [{"name": {"type": "sessionFlags"}, "config": session_config}]}, []
        assert method == "mcpServerStatus/list"
        return {"data": [], "nextCursor": None}, []

    monkeypatch.setattr(transport, "_rpc", rpc)
    await transport._verify_mission_config(request)

    effective["model_providers"]["attacker"] = dict(provider_row)
    with pytest.raises(RuntimeError, match="provider isolation"):
        await transport._verify_mission_config(request)


def test_codex_semantic_steps_ignore_message_deltas_but_count_completed_tools():
    deltas = [{"method": "item/agentMessage/delta", "params": {"delta": "x"}} for _ in range(101)]
    assert codex_module._semantic_step_count(deltas) == 0
    lifecycle = [
        {"method": "item/started", "params": {"item": {"id": "tool-1", "type": "commandExecution"}}},
        {"method": "item/completed", "params": {"item": {"id": "tool-1", "type": "commandExecution"}}},
        # A completion-only protocol stream is counted conservatively too.
        {"method": "item/completed", "params": {"item": {"id": "tool-2", "type": "mcpToolCall"}}},
    ]
    assert codex_module._semantic_step_count([*deltas, *lifecycle]) == 2


@pytest.mark.asyncio
async def test_codex_live_step_budget_interrupts_before_later_notifications(tmp_path: Path, monkeypatch):
    request = _request(
        tmp_path,
        config=HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("x")),
        step_budget=1,
    )
    deltas = [{"method": "item/agentMessage/delta", "params": {"delta": "narration"}} for _ in range(101)]
    messages = [
        *deltas,
        {"method": "item/started", "params": {"item": {"id": "tool-1", "type": "commandExecution"}}},
        {"method": "item/completed", "params": {"item": {"id": "tool-1", "type": "commandExecution"}}},
        # The second start is never allowed to complete. Its completion and
        # turn completion are deliberately left unread after interruption.
        {"method": "item/started", "params": {"item": {"id": "tool-2", "type": "fileChange"}}},
        {"method": "item/completed", "params": {"item": {"id": "tool-2", "type": "fileChange"}}},
        {"method": "turn/completed", "params": {"turn": {"id": "turn_1", "status": "completed"}}},
    ]
    class Stream:
        def __init__(self): self.remaining = list(messages); self.read = 0
        async def readline(self):
            self.read += 1
            if self.read == len(deltas) + 2:
                (tmp_path / "partial.txt").write_text("partial tool write", encoding="utf-8")
            return (json.dumps(self.remaining.pop(0)) + "\n").encode()
    stream = Stream()
    transport = CodexAppServerTransport(executable="codex")
    transport._process = SimpleNamespace(stdout=stream, returncode=None)  # type: ignore[assignment]
    sent: list[dict[str, object]] = []; terminated: list[object] = []
    async def start(_request): return None
    async def rpc(method, _params):
        assert method == "turn/start"
        return {"turn": {"id": "turn_1"}}, []
    async def send(message): sent.append(dict(message))
    async def terminate(process): terminated.append(process)
    monkeypatch.setattr(transport, "_start", start)
    monkeypatch.setattr(transport, "_rpc", rpc)
    monkeypatch.setattr(transport, "_send", send)
    monkeypatch.setattr(transport, "_terminate_process_group", terminate)

    result = await transport._run_turn(request, "thread_1")

    assert result["status"] is TerminalStatus.FAILED
    assert result["error"]["code"] == "step_budget_exceeded"
    assert result["steps"] == 2  # 101 deltas were not semantic steps.
    assert result["changed_files"] == (Path("partial.txt"),)
    assert stream.read == len(deltas) + 3
    assert len(stream.remaining) == 2
    assert [message["method"] for message in sent] == ["turn/interrupt"]
    assert terminated == [transport._process]
    assert transport._active_turns == {}


@pytest.mark.asyncio
async def test_codex_completion_only_step_fallback_still_interrupts_at_limit(tmp_path: Path, monkeypatch):
    request = _request(tmp_path, config=HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("x")), step_budget=1)
    class Stream:
        def __init__(self):
            self.remaining = [
                {"method": "item/completed", "params": {"item": {"id": "tool-1", "type": "commandExecution"}}},
                {"method": "item/completed", "params": {"item": {"id": "tool-2", "type": "fileChange"}}},
                {"method": "turn/completed", "params": {"turn": {"id": "turn_1", "status": "completed"}}},
            ]; self.read = 0
        async def readline(self):
            self.read += 1
            return (json.dumps(self.remaining.pop(0)) + "\n").encode()
    stream = Stream(); transport = CodexAppServerTransport(executable="codex")
    transport._process = SimpleNamespace(stdout=stream, returncode=None)  # type: ignore[assignment]
    sent: list[dict[str, object]] = []; terminated: list[object] = []
    async def start(_request): return None
    async def rpc(method, _params):
        assert method == "turn/start"; return {"turn": {"id": "turn_1"}}, []
    async def send(message): sent.append(dict(message))
    async def terminate(process): terminated.append(process)
    monkeypatch.setattr(transport, "_start", start); monkeypatch.setattr(transport, "_rpc", rpc)
    monkeypatch.setattr(transport, "_send", send); monkeypatch.setattr(transport, "_terminate_process_group", terminate)

    result = await transport._run_turn(request, "thread_1")

    assert result["error"]["code"] == "step_budget_exceeded" and result["steps"] == 2
    assert stream.read == 2 and len(stream.remaining) == 1
    assert [message["method"] for message in sent] == ["turn/interrupt"] and terminated == [transport._process]


@pytest.mark.asyncio
async def test_codex_close_terminates_then_kills_process_group_and_cleans_manifest(tmp_path: Path, monkeypatch) -> None:
    request = _isolated_request(tmp_path)
    spec, manifest = _isolation_spec(tmp_path, request)
    signals: list[tuple[int, int]] = []

    class Process:
        pid = 4321
        returncode = None
        stdin = None
        async def wait(self):
            waits.append("wait")

    waits: list[str] = []
    async def time_out(awaitable, timeout):
        await awaitable
        raise asyncio.TimeoutError
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(asyncio, "wait_for", time_out)
    transport = CodexAppServerTransport(isolation_spec=spec)
    transport._process = Process()  # type: ignore[assignment]

    await transport.close()

    assert [(pid, sig) for pid, sig in signals if sig] == [(4321, signal.SIGTERM), (4321, signal.SIGKILL)]
    assert len(waits) >= 2 and not manifest.exists()


@pytest.mark.asyncio
async def test_codex_close_already_exited_and_startup_failure_clean_manifest(tmp_path: Path, monkeypatch) -> None:
    request = _isolated_request(tmp_path)
    spec, manifest = _isolation_spec(tmp_path, request)
    transport = CodexAppServerTransport(isolation_spec=spec)
    transport._process = SimpleNamespace(returncode=0)  # type: ignore[assignment]
    await transport.close()
    assert not manifest.exists()

    startup = tmp_path / "startup"; startup.mkdir()
    request = _isolated_request(startup)
    spec, manifest = _isolation_spec(startup, request)
    executable = tmp_path / "startup" / "codex"; executable.write_text("", encoding="utf-8"); executable.chmod(0o755)
    class StartupProcess:
        pid = 9999
        returncode = None
        stdin = stdout = stderr = None
        async def wait(self): self.returncode = -15
    launched = StartupProcess()
    async def create(*args, **kwargs): return launched
    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(os, "killpg", lambda *_args: None)
    with pytest.raises(RuntimeError, match="not running"):
        await CodexAppServerTransport(executable=str(executable), isolation_spec=spec)._start(request)
    assert not manifest.exists()


@pytest.mark.asyncio
async def test_codex_isolation_spec_binds_a_canonical_mission_request(tmp_path: Path) -> None:
    request = _isolated_request(tmp_path)
    spec, _ = _isolation_spec(tmp_path, request)
    transport = CodexAppServerTransport(isolation_spec=spec)
    # A pre-existing live process exercises the critical early-return path:
    # binding must still happen before the transport reuses that process.
    transport._process = SimpleNamespace(returncode=None)  # type: ignore[assignment]

    await transport._start(request)

    assert transport._bound_request_fingerprint == spec.request_fingerprint(request)
    assert not hasattr(transport, "isolation_launcher")
    assert not hasattr(transport, "isolation_manifest")


@pytest.mark.asyncio
async def test_codex_isolation_rejects_manifest_tamper_and_inode_replacement_before_launch(tmp_path: Path, monkeypatch) -> None:
    launches: list[tuple[object, ...]] = []

    async def launched(*args, **kwargs):
        launches.append(args)
        raise AssertionError("subprocess launch must not occur")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", launched)
    request = _isolated_request(tmp_path)
    spec, manifest = _isolation_spec(tmp_path, request)
    manifest.write_text("{}", encoding="utf-8")
    manifest.chmod(0o600)
    with pytest.raises(RuntimeError, match="launch material changed"):
        await CodexAppServerTransport(isolation_spec=spec)._start(request)

    inode_dir = tmp_path / "inode"
    inode_dir.mkdir()
    inode_request = _isolated_request(inode_dir)
    inode_spec, inode_manifest = _isolation_spec(inode_dir, inode_request)
    replacement = inode_dir / "replacement.json"
    replacement.write_text(inode_manifest.read_text(encoding="utf-8"), encoding="utf-8")
    replacement.chmod(0o600)
    os.replace(replacement, inode_manifest)
    with pytest.raises(RuntimeError, match="launch material changed"):
        await CodexAppServerTransport(isolation_spec=inode_spec)._start(inode_request)
    assert launches == []


@pytest.mark.asyncio
async def test_codex_isolation_rejects_scope_or_network_mismatches_before_thread_or_turn(tmp_path: Path) -> None:
    request = _isolated_request(tmp_path)
    spec, _ = _isolation_spec(tmp_path, request)
    other_workspace = tmp_path / "other-workspace"
    other_workspace.mkdir()
    mismatches = (
        _isolated_request(other_workspace),
        _isolated_request(tmp_path, read_paths=(tmp_path / "input-a",)),
        _isolated_request(tmp_path, write_paths=()),
        _isolated_request(tmp_path, metadata={"mission_id": "mission_2"}),
        _isolated_request(tmp_path, metadata={"run_id": "run_2"}),
        _isolated_request(tmp_path, metadata={"agent_id": "agent_2"}),
        _isolated_request(tmp_path, network_policy=NetworkPolicy.ALLOW),
    )
    for mismatch in mismatches:
        transport = CodexAppServerTransport(isolation_spec=spec)
        transport._process = SimpleNamespace(returncode=None)  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="manifest does not bind request|deny network"):
            await transport._start(mismatch)
        with pytest.raises(RuntimeError, match="manifest does not bind request|deny network"):
            await transport.create_thread(request=mismatch, thread_id="thread_1")
        with pytest.raises(RuntimeError, match="manifest does not bind request|deny network"):
            await transport.run(request=mismatch, thread_id="thread_1")


@pytest.mark.asyncio
async def test_codex_isolation_transport_cannot_be_reused_across_scopes(tmp_path: Path) -> None:
    request = _isolated_request(tmp_path)
    spec, _ = _isolation_spec(tmp_path, request)
    transport = CodexAppServerTransport(isolation_spec=spec)
    transport._process = SimpleNamespace(returncode=None)  # type: ignore[assignment]
    await transport._start(request)

    same_manifest_different_scope = _isolated_request(tmp_path, role="reviewer")
    with pytest.raises(RuntimeError, match="cannot be reused across Mission scopes"):
        await transport._start(same_manifest_different_scope)


@pytest.mark.asyncio
async def test_codex_app_server_transport_executes_official_jsonl_contract(tmp_path: Path) -> None:
    executable = tmp_path / "fake-codex"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import pathlib
import sys

for raw in sys.stdin:
    message = json.loads(raw)
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}
    if request_id is None:
        continue
    if method == "initialize":
        result = {"userAgent": "fake-codex"}
    elif method == "config/read":
        cwd = params["cwd"]
        result = {"config": {"projects": {cwd: {"trust_level": "untrusted"}}, "model_provider": "openai", "openai_base_url": "https://api.openai.com/v1", "model_providers": {}, "mcp_servers": {}}, "origins": {"model_provider": {"name": {"type": "sessionFlags"}}, "openai_base_url": {"name": {"type": "sessionFlags"}}}, "layers": [{"name": {"type": "sessionFlags"}, "config": {"projects": {cwd: {"trust_level": "untrusted"}}, "model_provider": "openai", "openai_base_url": "https://api.openai.com/v1"}}]}
    elif method == "mcpServerStatus/list":
        result = {"data": [], "nextCursor": None}
    elif method == "skills/list":
        result = {"data": []}
    elif method == "thread/start":
        pathlib.Path(params["cwd"]).joinpath("thread-protocol.json").write_text(json.dumps(params), encoding="utf-8")
        result = {"thread": {"id": "thread_test"}}
    elif method == "thread/resume":
        result = {"thread": {"id": params["threadId"]}}
    elif method == "turn/start":
        cwd = pathlib.Path(params["cwd"])
        (cwd / "artifact.txt").write_text("built by app server", encoding="utf-8")
        (cwd / "protocol.json").write_text(json.dumps(params), encoding="utf-8")
        result = {"turn": {"id": "turn_test"}}
    else:
        result = {}
    print(json.dumps({"id": request_id, "result": result}), flush=True)
    if method == "turn/start":
        print(json.dumps({"method": "item/agentMessage/delta", "params": {"delta": "{\\\"reply\\\":\\\"done\\\",\\\"request\\\":\\\"await_user\\\"}"}}), flush=True)
        print(json.dumps({"method": "turn/completed", "params": {"turn": {"id": "turn_test", "status": "completed"}}}), flush=True)
""",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    config = HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("codex-test"))
    request = _request(
        tmp_path,
        config=config,
        prompt="build the approved application",
        metadata={"output_schema": {"type": "object"}},
    )
    transport = CodexAppServerTransport(executable=str(executable))
    harness = CodexHarness(transport)

    result = await harness.run(request)

    assert result.status is TerminalStatus.SUCCEEDED
    assert result.structured_output == {"reply": "done", "request": "await_user"}
    assert {path.name for path in result.changed_files} >= {"artifact.txt", "protocol.json"}
    protocol = json.loads((tmp_path / "protocol.json").read_text())
    thread_protocol = json.loads((tmp_path / "thread-protocol.json").read_text())
    assert thread_protocol["sandbox"] == "workspace-write"
    assert thread_protocol["approvalPolicy"] == "never"
    assert thread_protocol["ephemeral"] is False
    assert protocol["approvalPolicy"] == "never"
    assert protocol["sandboxPolicy"]["type"] == "workspaceWrite"
    assert protocol["sandboxPolicy"]["networkAccess"] is False
    assert protocol["outputSchema"] == {"type": "object"}
    assert transport._process is None

    ephemeral_request = _request(
        tmp_path,
        task_type=TaskType.CHAT,
        write_paths=(),
        config=config,
        session_mode="ephemeral",
    )
    ephemeral_transport = CodexAppServerTransport(executable=str(executable))
    try:
        thread_id = await ephemeral_transport.create_thread(request=ephemeral_request)
        ephemeral_protocol = json.loads((tmp_path / "thread-protocol.json").read_text())
        assert ephemeral_protocol["sandbox"] == "read-only"
        assert ephemeral_protocol["ephemeral"] is True
        with pytest.raises(ValueError, match="ephemeral"):
            await ephemeral_transport.create_thread(request=ephemeral_request, thread_id=thread_id)
    finally:
        await ephemeral_transport.close()
    assert ephemeral_transport._process is None


@pytest.mark.asyncio
async def test_installed_codex_app_server_accepts_thread_start_handshake(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the installed official schema without starting a model turn."""
    executable = shutil.which("codex")
    if executable is None:
        pytest.skip("codex executable is not installed")
    # A direct transport integration must model the production launcher's
    # private durable state. Otherwise the developer's user configuration can
    # merge credential-bearing MCP or provider rows into this Mission process.
    ambient_home = tmp_path_factory.mktemp("ambient-home")
    ambient_codex = ambient_home / ".codex"
    ambient_codex.mkdir()
    (ambient_codex / "config.toml").write_text(
        """[model_providers.poison]
name = "poison"
base_url = "https://example.invalid/v1"
wire_api = "responses"

[mcp_servers.poison]
command = "/usr/bin/false"
""",
        encoding="utf-8",
    )
    codex_home = tmp_path_factory.mktemp("mission-codex-home")
    monkeypatch.setenv("HOME", str(ambient_home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transport = CodexAppServerTransport(executable=executable)
    request = _request(
        tmp_path,
        config=HarnessConfig("codex", ProviderConfig("openai"), ModelCapability("default")),
    )
    process = None
    try:
        thread_id = await asyncio.wait_for(transport.create_thread(request=request), timeout=10)
        process = transport._process
        assert thread_id
        ephemeral_request = _request(
            tmp_path,
            task_type=TaskType.CHAT,
            write_paths=(),
            config=request.config,
            session_mode="ephemeral",
        )
        assert await asyncio.wait_for(transport.create_thread(request=ephemeral_request), timeout=10)
    except RuntimeError as exc:
        detail = str(exc).lower()
        if ".codex" in detail and ("permission denied" in detail or "operation not permitted" in detail):
            pytest.skip("installed Codex cannot initialize its writable state in this environment")
        raise
    finally:
        await transport.close()
    if process is not None:
        assert process.returncode is not None


@pytest.mark.asyncio
async def test_ephemeral_request_never_creates_or_resumes_a_durable_session(tmp_path: Path) -> None:
    harness = FakeHarness()
    request = _request(
        tmp_path, task_type=TaskType.CHAT, write_paths=(), session_mode="ephemeral",
    )
    result = await harness.run(request)

    assert result.status is TerminalStatus.SUCCEEDED
    assert not (tmp_path / ".cmul8/harness/sessions.json").exists()
    with pytest.raises(ValueError, match="ephemeral"):
        await harness.create_session(request)
    with pytest.raises(ValueError, match="ephemeral"):
        await harness.resume_session(request)


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


def test_provider_extra_rejects_credentials_and_metadata_cannot_leak_them() -> None:
    safe = ProviderConfig("custom", extra={"request_timeout": 30, "region": "us-east"})
    assert safe.metadata()["extra"] == {"request_timeout": 30, "region": "us-east"}
    for kwargs in (
        {"extra": {"api_key": "do-not-serialize-me"}},
        {"extra": {"option": "token=do-not-serialize-me"}},
        {"endpoint": "https://user:do-not-serialize-me@example.test/v1"},
        {"extra": {"endpoint": "https://user:do-not-serialize-me@example.test/v1"}},
        {"endpoint": "https://example.test/v1?api_key=do-not-serialize-me"},
        {"extra": {"unknown": "opaque-value"}},
        {"extra": {"region": object()}},
        {"endpoint": 0},
        {"credential_env_var": 0},
        {"extra": ("request_timeout", 30)},
        {"extra": {"request_timeout": float("nan")}},
        {"extra": {"request_timeout": float("inf")}},
        {"extra": {"request_timeout": float("-inf")}},
        {"extra": {"request_timeout": True}},
    ):
        with pytest.raises(ValueError):
            ProviderConfig("custom", **kwargs)


def test_session_repository_rejects_symlink_components_and_serializes_process_saves(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, workspace / ".cmul8")
    with pytest.raises(PermissionError):
        JsonSessionRepository(workspace).save(AgentSession(
            session_id="bad", project_id="proj", role="builder", harness="fake", provider="custom", model_id="test",
        ))
    assert not (outside / "harness/sessions.json").exists()
    (workspace / ".cmul8").unlink()
    (workspace / ".cmul8").mkdir()
    os.symlink(outside, workspace / ".cmul8" / "harness")
    with pytest.raises(PermissionError):
        JsonSessionRepository(workspace).get("proj", "builder")
    (workspace / ".cmul8" / "harness").unlink()

    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_concurrent_session_save, args=(str(workspace), f"role-{index}")) for index in range(8)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    data = json.loads((workspace / ".cmul8/harness/sessions.json").read_text())
    assert set(data["sessions"]) == {f"proj:role-{index}" for index in range(8)}


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
async def test_resume_rejects_environment_and_full_configuration_identity_mismatch(tmp_path: Path) -> None:
    harness = FakeHarness()
    request = _request(tmp_path)
    await harness.create_session(request)
    changed_environment = _request(tmp_path, environment_id="env_other")
    changed_model = _request(tmp_path, config=HarnessConfig("fake", ProviderConfig("custom"), ModelCapability("other")))
    changed_controls = _request(tmp_path, config=HarnessConfig(
        "fake", ProviderConfig("custom"), ModelCapability("test"), model_reasoning_effort="high", codex_profile="strict",
    ))
    changed_endpoint = _request(tmp_path, config=HarnessConfig(
        "fake", ProviderConfig("custom", endpoint="https://model.example/v1", credential_env_var="CMUL8_TEST_TOKEN"), ModelCapability("test"),
    ))
    changed_credential_name = _request(tmp_path, config=HarnessConfig(
        "fake", ProviderConfig("custom", credential_env_var="CMUL8_OTHER_TOKEN"), ModelCapability("test"),
    ))
    changed_extra = _request(tmp_path, config=HarnessConfig(
        "fake", ProviderConfig("custom", credential_env_var="CMUL8_TEST_TOKEN", extra={"region": "us-east"}), ModelCapability("test"),
    ))
    for mismatch in (changed_environment, changed_model, changed_controls, changed_endpoint, changed_credential_name, changed_extra):
        with pytest.raises(ValueError, match="configuration identity"):
            await harness.resume_session(mismatch)


@pytest.mark.asyncio
async def test_prime_internal_type_error_is_not_retried(tmp_path: Path) -> None:
    calls = 0

    def runner(*, request, session):
        nonlocal calls
        calls += 1
        raise TypeError("internal adapter failure")

    config = HarnessConfig("prime", ProviderConfig("custom"), ModelCapability("test"))
    request = _request(tmp_path, task_type=TaskType.CHAT, write_paths=(), config=config)
    result = await PrimeHarness(runner).run(request)
    assert calls == 1
    assert result.status is TerminalStatus.FAILED
    assert result.error and result.error["code"] == "provider_error"


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
