"""Recursive credential screening and JSON freezing helpers."""

from __future__ import annotations

import re
from types import MappingProxyType
from typing import Any, Mapping

from .errors import CredentialPolicyError

_CREDENTIAL_TERMS = {
	"access_key", "access_token", "api_key", "auth", "authorization", "bearer",
	"authentication", "client_secret", "credential", "credentials", "password", "private_key", "secret", "token",
}
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
_AWS_KEY_RE = re.compile(r"^(?:AKIA|ASIA)[A-Z0-9]{16}$")
_CREDENTIAL_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@:\s]+:[^/@\s]+@", re.IGNORECASE)


def _normalized_key(key: Any) -> str:
	return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def _credential_key(key: str) -> bool:
	parts = set(filter(None, key.split("_")))
	compound_match = any(key.startswith(f"{term}_") or key.endswith(f"_{term}") for term in _CREDENTIAL_TERMS)
	return key in _CREDENTIAL_TERMS or compound_match or bool(parts & {
		"auth", "authentication", "authorization", "bearer", "credential", "credentials",
		"password", "secret", "token",
	})


def _credential_value(value: str) -> bool:
	text = value.strip()
	lower = text.lower()
	return (
		lower.startswith(("bearer ", "basic ", "token ", "sk-", "sk_", "xoxb-", "xoxp-", "ghp_", "github_pat_", "npm_"))
		or "-----begin private key-----" in lower
		or "-----begin rsa private key-----" in lower
		or any(marker in lower for marker in ("access_token=", "api_key=", "client_secret=", "password="))
		or bool(_JWT_RE.fullmatch(text))
		or bool(_AWS_KEY_RE.fullmatch(text))
		or bool(_CREDENTIAL_URL_RE.match(text))
	)


def assert_opaque_credentials(value: Any, *, context: str, path: str = "$") -> None:
	"""Reject raw credentials recursively; opaque references remain allowed."""
	if isinstance(value, Mapping):
		for raw_key, child in value.items():
			key = _normalized_key(raw_key)
			child_path = f"{path}.{raw_key}"
			if _credential_key(key):
				if not key.endswith("_ref") or not isinstance(child, str) or not child.strip():
					raise CredentialPolicyError(f"{context} contains raw credential-like field at {child_path}; use an opaque *_ref")
			assert_opaque_credentials(child, context=context, path=child_path)
		return
	if isinstance(value, (list, tuple)):
		for index, child in enumerate(value):
			assert_opaque_credentials(child, context=context, path=f"{path}[{index}]")
		return
	if isinstance(value, str) and _credential_value(value):
		raise CredentialPolicyError(f"{context} contains credential-like value at {path}; use an opaque reference")


def freeze_json(value: Any) -> Any:
	if isinstance(value, Mapping):
		return MappingProxyType({str(key): freeze_json(child) for key, child in value.items()})
	if isinstance(value, list):
		return tuple(freeze_json(child) for child in value)
	return value


def thaw_json(value: Any) -> Any:
	if isinstance(value, Mapping):
		return {str(key): thaw_json(child) for key, child in value.items()}
	if isinstance(value, (tuple, list)):
		return [thaw_json(child) for child in value]
	return value
