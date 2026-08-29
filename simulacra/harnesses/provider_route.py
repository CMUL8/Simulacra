"""Backend-neutral, operator-owned Responses model routing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from .contracts import HarnessConfig, ProviderConfig


OPENAI_BASE_URL = "https://api.openai.com/v1"
CUSTOM_CREDENTIAL_ENV = "CMUL8_MODEL_API_KEY"
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")


@dataclass(frozen=True, slots=True)
class ResponsesProviderRoute:
    """A model route shared by every certified Mission execution backend."""

    provider: str
    endpoint: str
    credential_env_var: str | None

    def __post_init__(self) -> None:
        if self.provider == "openai":
            if self.endpoint != OPENAI_BASE_URL or self.credential_env_var != "OPENAI_API_KEY":
                raise ValueError("invalid OpenAI model route")
            return
        if self.provider != "custom":
            raise ValueError("unsupported Mission model provider")
        parsed = urlsplit(self.endpoint)
        if (
            parsed.scheme != "https" or not parsed.netloc
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
        ):
            raise ValueError("custom model route requires a credential-free HTTPS base URL")
        if self.credential_env_var not in {None, CUSTOM_CREDENTIAL_ENV}:
            raise ValueError("custom model route uses an unsupported credential variable")

    @classmethod
    def from_config(cls, config: HarnessConfig) -> "ResponsesProviderRoute":
        if not _MODEL_ID.fullmatch(config.model.model_id):
            raise ValueError("invalid Mission model selection")
        if config.provider.provider == "openai":
            if (
                config.provider.endpoint not in {None, OPENAI_BASE_URL}
                or config.provider.credential_env_var not in {None, "OPENAI_API_KEY"}
            ):
                raise ValueError("invalid OpenAI model route")
            return cls("openai", OPENAI_BASE_URL, "OPENAI_API_KEY")
        if config.provider.provider == "custom":
            if not config.provider.endpoint:
                raise ValueError("custom model route requires a base URL")
            return cls("custom", config.provider.endpoint, config.provider.credential_env_var)
        raise ValueError("unsupported Mission model provider")

    @classmethod
    def from_manifest(cls, value: Mapping[str, object] | object) -> "ResponsesProviderRoute":
        """Restore one previously admitted credential-free route."""
        if not isinstance(value, Mapping) or set(value) != {
            "provider", "endpoint", "credential_env_var",
        }:
            raise ValueError("invalid persisted Mission model route")
        provider, endpoint = value.get("provider"), value.get("endpoint")
        credential = value.get("credential_env_var")
        if not isinstance(provider, str) or not isinstance(endpoint, str):
            raise ValueError("invalid persisted Mission model route")
        if credential is not None and not isinstance(credential, str):
            raise ValueError("invalid persisted Mission model route")
        return cls(provider, endpoint, credential)

    def provider_config(self) -> ProviderConfig:
        return ProviderConfig(
            self.provider,
            endpoint=self.endpoint,
            credential_env_var=self.credential_env_var,
        )

    def to_manifest(self) -> dict[str, str | None]:
        return {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "credential_env_var": self.credential_env_var,
        }

    def credential_ready(self, environ: Mapping[str, str]) -> bool:
        return self.credential_env_var is None or bool(str(environ.get(self.credential_env_var, "")).strip())

    def allowed_environment_names(self) -> frozenset[str]:
        if self.provider == "openai":
            return frozenset({"OPENAI_API_KEY", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"})
        return frozenset({self.credential_env_var}) if self.credential_env_var else frozenset()
