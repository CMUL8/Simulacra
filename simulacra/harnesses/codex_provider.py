"""Fail-closed, operator-owned Codex model-provider routing."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .contracts import HarnessConfig
from .provider_route import (
    CUSTOM_CREDENTIAL_ENV,
    OPENAI_BASE_URL,
    ResponsesProviderRoute,
)


CUSTOM_PROVIDER_ID = "cmul8_open"
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


def _project_override(workspace: Path) -> str:
    rendered = str(workspace.resolve())
    if any(ord(character) < 32 for character in rendered):
        raise RuntimeError("unsafe Mission workspace path")
    return f"projects={{{json.dumps(rendered)}={{trust_level=\"untrusted\"}}}}"


@dataclass(frozen=True, slots=True)
class CodexProviderRoute:
    provider: str
    endpoint: str
    credential_env_var: str | None

    def __post_init__(self) -> None:
        ResponsesProviderRoute(self.provider, self.endpoint, self.credential_env_var)

    @classmethod
    def from_config(cls, config: HarnessConfig) -> "CodexProviderRoute":
        if config.harness != "codex" or not _MODEL_ID.fullmatch(config.model.model_id):
            raise ValueError("invalid Codex Mission model selection")
        route = ResponsesProviderRoute.from_config(config)
        return cls(route.provider, route.endpoint, route.credential_env_var)

    @classmethod
    def from_manifest(cls, value: object) -> "CodexProviderRoute":
        if not isinstance(value, Mapping) or set(value) != {"provider", "endpoint", "credential_env_var"}:
            raise ValueError("invalid manifest model route")
        credential = value.get("credential_env_var")
        if credential is not None and not isinstance(credential, str):
            raise ValueError("invalid manifest model credential")
        return cls(str(value.get("provider") or ""), str(value.get("endpoint") or ""), credential)

    def to_manifest(self) -> dict[str, str | None]:
        return {"provider": self.provider, "endpoint": self.endpoint, "credential_env_var": self.credential_env_var}

    @property
    def codex_provider_id(self) -> str:
        return "openai" if self.provider == "openai" else CUSTOM_PROVIDER_ID

    def minimal_provider_table(self) -> dict[str, dict[str, object]]:
        if self.provider == "openai":
            return {}
        row: dict[str, object] = {
            "name": "CMUL8 Open Models",
            "base_url": self.endpoint,
            "wire_api": "responses",
            "requires_openai_auth": False,
        }
        if self.credential_env_var:
            row["env_key"] = self.credential_env_var
        return {CUSTOM_PROVIDER_ID: row}

    def config_args(self) -> tuple[str, ...]:
        values = [
            f"model_provider={json.dumps(self.codex_provider_id)}",
            f"openai_base_url={json.dumps(OPENAI_BASE_URL)}",
        ]
        if self.provider == "custom":
            env_key = f",env_key={json.dumps(self.credential_env_var)}" if self.credential_env_var else ""
            values.append(
                "model_providers={cmul8_open={"
                f"name=\"CMUL8 Open Models\",base_url={json.dumps(self.endpoint)}{env_key},"
                "wire_api=\"responses\",requires_openai_auth=false}}"
            )
        output: list[str] = []
        for value in values:
            output.extend(("-c", value))
        return tuple(output)

    def allowed_environment_names(self) -> frozenset[str]:
        if self.provider == "openai":
            return frozenset({"OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"})
        return frozenset({self.credential_env_var}) if self.credential_env_var else frozenset()

    def credential_ready(self, environ: Mapping[str, str]) -> bool:
        return self.credential_env_var is None or bool(str(environ.get(self.credential_env_var, "")).strip())


def mission_app_server_args(workspace: Path, route: CodexProviderRoute) -> tuple[str, ...]:
    return (
        "--strict-config",
        "-c", 'shell_environment_policy.inherit="none"',
        "-c", "shell_environment_policy.ignore_default_excludes=false",
        "-c", "mcp_servers={}",
        "-c", _project_override(workspace),
        *route.config_args(),
        "-c", "project_doc_max_bytes=0",
        "-c", "agents.enabled=false",
        "-c", "allow_login_shell=false",
        "-c", "check_for_update_on_startup=false",
        "--disable", "plugins",
        "--disable", "remote_plugin",
        "--disable", "recommended_plugins",
        "--disable", "apps",
        "--disable", "hooks",
        "--disable", "multi_agent",
        "--disable", "skill_search",
        "--disable", "skill_mcp_dependency_install",
        "app-server",
        "--listen",
        "stdio://",
    )
