"""Credential screening for persisted Operation Graph connector configuration."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .errors import CredentialPolicyError

_CREDENTIAL_TERMS = {
	"access_key", "access_token", "api_key", "auth", "authorization", "bearer",
	"authentication", "client_secret", "credential", "credentials", "password", "private_key", "secret", "token",
}
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")
_AWS_KEY_RE = re.compile(r"^(?:AKIA|ASIA)[A-Z0-9]{16}$")
_CREDENTIAL_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@:\s]+:[^/@\s]+@", re.IGNORECASE)
_CREDENTIAL_QUERY_RE = re.compile(
	r"(?:[?&]|^)(?:access[_-]?token|accesstoken|api[_-]?key|apikey|client[_-]?secret|clientsecret|"
	r"authorization|password|secret|token)=",
	re.IGNORECASE,
)
_CONTEXTUAL_NAME_FIELDS = {"name", "key", "header_name", "parameter_name", "query_name"}
_CONTEXTUAL_VALUE_FIELDS = {"value", "header_value", "parameter_value", "query_value"}
_CONTEXTUAL_REFERENCE_FIELDS = {
	"credential_ref", "secret_ref", "token_ref", "access_token_ref", "api_key_ref",
	"authorization_ref", "auth_ref", "value_ref", "header_value_ref", "parameter_value_ref", "query_value_ref",
}


def _normalized_key(key: Any) -> str:
	text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key).strip())
	return text.lower().replace("-", "_").replace(" ", "_")


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
		or bool(_CREDENTIAL_QUERY_RE.search(text))
		or bool(_JWT_RE.fullmatch(text))
		or bool(_AWS_KEY_RE.fullmatch(text))
		or bool(_CREDENTIAL_URL_RE.match(text))
	)


def _explicit_contextual_reference(value: Any) -> bool:
	"""Return true only when this one contextual value is all opaque references."""
	if not isinstance(value, Mapping) or not value:
		return False
	return all(
		key in _CONTEXTUAL_REFERENCE_FIELDS and isinstance(child, str) and child.strip()
		for key, child in ((_normalized_key(raw), item) for raw, item in value.items())
	)


def _contextual_credential_value_path(value: Mapping[Any, Any], *, path: str) -> str | None:
	"""Find raw header/query parameter pairs whose names carry credential meaning."""
	items = [(_normalized_key(raw_key), child, str(raw_key)) for raw_key, child in value.items()]
	credential_name = any(
		key in _CONTEXTUAL_NAME_FIELDS and isinstance(child, str) and _credential_key(_normalized_key(child))
		for key, child, _raw_key in items
	)
	if not credential_name:
		return None
	for key, child, raw_key in items:
		if key in _CONTEXTUAL_VALUE_FIELDS and not _explicit_contextual_reference(child):
			return f"{path}.{raw_key}"
	return None


def assert_opaque_credentials(value: Any, *, context: str, path: str = "$") -> None:
	"""Reject raw credentials recursively while permitting non-empty opaque *_ref values."""
	if isinstance(value, Mapping):
		contextual_path = _contextual_credential_value_path(value, path=path)
		if contextual_path is not None:
			raise CredentialPolicyError(
				f"{context} contains raw credential-like field at {contextual_path}; use an opaque *_ref"
			)
		for raw_key, child in value.items():
			key = _normalized_key(raw_key)
			child_path = f"{path}.{raw_key}"
			if _credential_key(key):
				if not key.endswith("_ref") or not isinstance(child, str) or not child.strip():
					raise CredentialPolicyError(
						f"{context} contains raw credential-like field at {child_path}; use an opaque *_ref"
					)
			assert_opaque_credentials(child, context=context, path=child_path)
		return
	if isinstance(value, (list, tuple)):
		for index, child in enumerate(value):
			assert_opaque_credentials(child, context=context, path=f"{path}[{index}]")
		return
	if isinstance(value, str) and _credential_value(value):
		raise CredentialPolicyError(f"{context} contains credential-like value at {path}; use an opaque reference")


def assert_connector_configurations_opaque(graph: Mapping[str, Any]) -> None:
	"""Apply the credential invariant to every connector configuration in a graph."""
	for connector in graph.get("connectors", ()):
		if not isinstance(connector, Mapping):
			continue
		assert_opaque_credentials(
			connector.get("configuration", {}),
			context="Operation Graph connector configuration",
		)
