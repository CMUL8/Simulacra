"""Recursive credential screening and JSON freezing helpers."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any, Mapping

from simulacra.operation_graph.security import assert_opaque_credentials as _assert_operation_graph_credentials

from .errors import CredentialPolicyError

def assert_opaque_credentials(value: Any, *, context: str, path: str = "$") -> None:
	"""Reject raw credentials recursively; opaque references remain allowed."""
	try:
		_assert_operation_graph_credentials(value, context=context, path=path)
	except ValueError as exc:
		# Runtime callers retain their established exception type while sharing the
		# immutable Operation Graph's exact screening semantics.
		raise CredentialPolicyError(str(exc)) from exc


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
