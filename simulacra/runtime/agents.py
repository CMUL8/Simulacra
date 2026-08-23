"""Graph-confined runtime-agent supervisor and explicit tool descriptors."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, TYPE_CHECKING

from .errors import RuntimeAuthorizationError
from .policy import ApprovedGraph
from .security import assert_opaque_credentials

if TYPE_CHECKING:
	from .models import ActionRecord
	from .services import ActionGateway

_FORBIDDEN_CLASSES = ("filesystem", "source", "process", "shell", "terminal", "exec", "code")
_EFFECTFUL_TERMS = (".write", ".send", ".delete", ".create", ".update", ".publish", ".execute")
def _normalized(value: str) -> str:
	return value.strip().lower().replace("-", ".").replace("_", ".")


class RuntimeTool(Protocol):
	name: str
	capability: str
	tool_class: str


@dataclass(frozen=True)
class ReadOnlyDataTool:
	"""Trusted data-query adapter. It may return data but may not cause effects."""

	name: str
	capability: str
	executor: Callable[[Mapping[str, Any]], Any]
	tool_class: str = "data.read"


@dataclass(frozen=True)
class ActionTool:
	"""Effectful tool descriptor with no direct executor bypass."""

	name: str
	capability: str
	connector_id: str
	operation: str
	tool_class: str = "action"


class RuntimeAgentSupervisor:
	def __init__(self, policy: ApprovedGraph, tools: Mapping[str, RuntimeTool] | None = None, *, action_gateway: ActionGateway | None = None):
		self.policy = policy
		self.action_gateway = action_gateway
		self.tools: dict[str, RuntimeTool] = {}
		for key, descriptor in (tools or {}).items():
			if not isinstance(descriptor, (ReadOnlyDataTool, ActionTool)):
				raise RuntimeAuthorizationError("runtime tools require explicit ReadOnlyDataTool or ActionTool descriptors")
			if key != descriptor.name:
				raise RuntimeAuthorizationError("runtime tool mapping key must equal descriptor name")
			self._validate_descriptor(descriptor)
			self.tools[key] = descriptor

	@staticmethod
	def _validate_descriptor(descriptor: RuntimeTool) -> None:
		classification = _normalized(descriptor.tool_class)
		capability = _normalized(descriptor.capability)
		if any(term in classification or term in capability for term in _FORBIDDEN_CLASSES):
			raise RuntimeAuthorizationError("runtime agents cannot receive filesystem, source, process, or shell tool classes")
		if isinstance(descriptor, ReadOnlyDataTool):
			if classification != "data.read" or any(term in capability for term in _EFFECTFUL_TERMS):
				raise RuntimeAuthorizationError("effectful runtime tools must use ActionTool")
		elif classification != "action":
			raise RuntimeAuthorizationError("ActionTool must use the action tool class")

	def invoke(
		self,
		agent_id: str,
		tool: str,
		payload: Mapping[str, Any],
		*,
		resource: str | None = None,
		idempotency_key: str | None = None,
	) -> Any | ActionRecord:
		assert_opaque_credentials(payload, context="runtime agent payload")
		descriptor = self.tools.get(tool)
		if descriptor is None:
			raise RuntimeAuthorizationError(f"runtime tool is unavailable: {tool}")
		self._validate_descriptor(descriptor)
		if not self.policy.agent_allows(agent_id, descriptor.capability, resource):
			raise RuntimeAuthorizationError(f"runtime agent capability is not graph-allowed: {descriptor.capability}")
		if isinstance(descriptor, ActionTool):
			if self.action_gateway is None:
				raise RuntimeAuthorizationError("runtime action gateway is unavailable")
			if not idempotency_key:
				raise RuntimeAuthorizationError("runtime action tools require an idempotency key")
			return self.action_gateway.submit(
				descriptor.connector_id,
				descriptor.operation,
				payload,
				requester_id=agent_id,
				idempotency_key=idempotency_key,
			)
		return descriptor.executor(copy.deepcopy(dict(payload)))

	invoke_tool = invoke


RuntimeAgentGateway = RuntimeAgentSupervisor
