"""Graph-confined runtime-agent supervisor."""

from __future__ import annotations

import copy
from typing import Any, Callable, Mapping

from .errors import RuntimeAuthorizationError
from .policy import ApprovedGraph

_SOURCE_TERMS = ("source", "code.write", "filesystem.write", "shell", "exec", "terminal")
_SECRET_KEYS = {"secret", "password", "token", "credential", "api_key", "private_key"}


def _contains_raw_secret(value: Any, key: str = "") -> bool:
	if isinstance(value, Mapping):
		for child_key, child in value.items():
			normalized = str(child_key).lower()
			if normalized in _SECRET_KEYS and not normalized.endswith("_ref"): return True
			if _contains_raw_secret(child, normalized): return True
	elif isinstance(value, list):
		return any(_contains_raw_secret(item, key) for item in value)
	return False


class RuntimeAgentSupervisor:
	def __init__(self, policy: ApprovedGraph, tools: Mapping[str, Callable[[Mapping[str, Any]], Any]] | None = None):
		self.policy, self.tools = policy, dict(tools or {})

	def invoke(self, agent_id: str, tool: str, payload: Mapping[str, Any], *, resource: str | None = None) -> Any:
		normalized = tool.lower().replace("-", ".").replace("_", ".")
		if any(term in normalized for term in _SOURCE_TERMS): raise RuntimeAuthorizationError("runtime agents cannot access source code or execution tools")
		if _contains_raw_secret(payload): raise RuntimeAuthorizationError("runtime agents receive opaque secret references, never raw credentials")
		if not self.policy.agent_allows(agent_id, tool, resource): raise RuntimeAuthorizationError(f"runtime agent tool is not graph-allowed: {tool}")
		executor = self.tools.get(tool)
		if executor is None: raise RuntimeAuthorizationError(f"runtime tool is unavailable: {tool}")
		return executor(copy.deepcopy(dict(payload)))

	invoke_tool = invoke


RuntimeAgentGateway = RuntimeAgentSupervisor
