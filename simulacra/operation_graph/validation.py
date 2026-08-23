from __future__ import annotations

import copy
import math
import re
from typing import Any, Mapping

from .errors import GraphValidationError, ValidationIssue

SCHEMA_ID = "cmul8.operation-graph.v0"
METADATA_FIELDS = frozenset(
	{"schema_id", "graph_id", "tenant_id", "project_id", "name", "version", "description", "migrated_from"}
)
AREAS = (
	"entities",
	"views",
	"workflows",
	"agents",
	"automations",
	"connectors",
	"permissions",
	"approval_rules",
	"schedules",
)
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_SOURCE_WRITE_CAPABILITIES = {
	"code.write",
	"filesystem.source.write",
	"source.write",
	"source_code.write",
	"write_code",
	"write_source",
}


def _json_issues(value: Any, path: str, issues: list[ValidationIssue]) -> None:
	if value is None or isinstance(value, (str, bool, int)):
		return
	if isinstance(value, float):
		if not math.isfinite(value):
			issues.append(ValidationIssue(path, "must be a finite JSON number"))
		return
	if isinstance(value, list):
		for index, item in enumerate(value):
			_json_issues(item, f"{path}[{index}]", issues)
		return
	if isinstance(value, Mapping):
		for key, item in value.items():
			if not isinstance(key, str):
				issues.append(ValidationIssue(path, "object keys must be strings"))
			else:
				_json_issues(item, f"{path}.{key}", issues)
		return
	issues.append(ValidationIssue(path, f"contains unsupported value of type {type(value).__name__}"))


def _required_string(value: Mapping[str, Any], key: str, path: str, issues: list[ValidationIssue]) -> None:
	if key not in value:
		issues.append(ValidationIssue(f"{path}.{key}", "is required"))
	elif not isinstance(value[key], str) or not value[key].strip():
		issues.append(ValidationIssue(f"{path}.{key}", "must be a non-empty string"))


def _reference(
	item: Mapping[str, Any],
	key: str,
	known: set[str],
	path: str,
	issues: list[ValidationIssue],
) -> None:
	if key in item and item[key] not in known:
		issues.append(ValidationIssue(f"{path}.{key}", f"references unknown id {item[key]!r}"))


def _string_list(
	item: Mapping[str, Any], key: str, path: str, issues: list[ValidationIssue], *, allow_empty: bool = True
) -> list[str]:
	value = item.get(key)
	if not isinstance(value, list) or (not allow_empty and not value):
		issues.append(ValidationIssue(f"{path}.{key}", "must be an array of non-empty strings"))
		return []
	strings: list[str] = []
	for index, member in enumerate(value):
		if not isinstance(member, str) or not member.strip():
			issues.append(ValidationIssue(f"{path}.{key}[{index}]", "must be a non-empty string"))
		else:
			strings.append(member)
	if len(strings) != len(set(strings)):
		issues.append(ValidationIssue(f"{path}.{key}", "must not contain duplicates"))
	return strings


def _string_values(value: Any) -> list[str]:
	if isinstance(value, str):
		return [value]
	if isinstance(value, list):
		result: list[str] = []
		for item in value:
			result.extend(_string_values(item))
		return result
	if isinstance(value, Mapping):
		result = []
		for item in value.values():
			result.extend(_string_values(item))
		return result
	return []


def validate_operation_graph(graph: Mapping[str, Any]) -> dict[str, Any]:
	"""Validate and return a detached graph, or raise path-oriented errors."""
	issues: list[ValidationIssue] = []
	if not isinstance(graph, Mapping):
		raise GraphValidationError([ValidationIssue("$", "must be an object")])
	_json_issues(graph, "$", issues)
	allowed = {"metadata", *AREAS}
	for key in graph:
		if key not in allowed:
			issues.append(ValidationIssue(f"$.{key}", "is not a supported top-level area"))

	metadata = graph.get("metadata")
	if not isinstance(metadata, Mapping):
		issues.append(ValidationIssue("$.metadata", "must be an object"))
		metadata = {}
	for key in ("schema_id", "graph_id", "tenant_id", "project_id", "name"):
		_required_string(metadata, key, "$.metadata", issues)
	for key in metadata:
		if key not in METADATA_FIELDS:
			issues.append(ValidationIssue(f"$.metadata.{key}", "is not a supported metadata field"))
	if metadata.get("schema_id") not in (None, SCHEMA_ID):
		issues.append(ValidationIssue("$.metadata.schema_id", f"must equal {SCHEMA_ID!r}"))
	for key in ("graph_id", "tenant_id", "project_id"):
		value = metadata.get(key)
		if isinstance(value, str) and value.strip() and not _ID_RE.fullmatch(value):
			issues.append(ValidationIssue(f"$.metadata.{key}", "contains unsafe characters"))
	for key in ("description", "migrated_from"):
		if key in metadata and not isinstance(metadata[key], str):
			issues.append(ValidationIssue(f"$.metadata.{key}", "must be a string"))
	version = metadata.get("version")
	if version is None:
		issues.append(ValidationIssue("$.metadata.version", "is required"))
	elif isinstance(version, bool) or not isinstance(version, int) or version < 0:
		issues.append(ValidationIssue("$.metadata.version", "must be a non-negative integer"))

	ids: dict[str, set[str]] = {}
	for area in AREAS:
		items = graph.get(area)
		if not isinstance(items, list):
			issues.append(ValidationIssue(f"$.{area}", "must be an array"))
			ids[area] = set()
			continue
		seen: set[str] = set()
		for index, item in enumerate(items):
			path = f"$.{area}[{index}]"
			if not isinstance(item, Mapping):
				issues.append(ValidationIssue(path, "must be an object"))
				continue
			_required_string(item, "id", path, issues)
			_required_string(item, "name", path, issues)
			item_id = item.get("id")
			if isinstance(item_id, str) and item_id:
				if not _ID_RE.fullmatch(item_id):
					issues.append(ValidationIssue(f"{path}.id", "contains unsafe characters"))
				if item_id in seen:
					issues.append(ValidationIssue(f"{path}.id", f"duplicates id {item_id!r}"))
				seen.add(item_id)
		ids[area] = seen

	for index, item in enumerate(graph.get("views", []) if isinstance(graph.get("views"), list) else []):
		if isinstance(item, Mapping):
			_required_string(item, "entity_id", f"$.views[{index}]", issues)
			_reference(item, "entity_id", ids["entities"], f"$.views[{index}]", issues)
	for index, item in enumerate(graph.get("entities", []) if isinstance(graph.get("entities"), list) else []):
		if not isinstance(item, Mapping):
			continue
		fields = item.get("fields")
		if not isinstance(fields, list):
			issues.append(ValidationIssue(f"$.entities[{index}].fields", "must be an array"))
			continue
		for field_index, field in enumerate(fields):
			field_path = f"$.entities[{index}].fields[{field_index}]"
			if not isinstance(field, Mapping):
				issues.append(ValidationIssue(field_path, "must be an object"))
				continue
			_required_string(field, "name", field_path, issues)
			_required_string(field, "type", field_path, issues)
	for index, item in enumerate(graph.get("workflows", []) if isinstance(graph.get("workflows"), list) else []):
		if isinstance(item, Mapping):
			path = f"$.workflows[{index}]"
			states = _string_list(item, "states", path, issues, allow_empty=False)
			_required_string(item, "initial_state", path, issues)
			if isinstance(item.get("initial_state"), str) and item["initial_state"] not in states:
				issues.append(ValidationIssue(f"{path}.initial_state", "must name one of the workflow states"))
	for index, item in enumerate(graph.get("automations", []) if isinstance(graph.get("automations"), list) else []):
		if isinstance(item, Mapping):
			_required_string(item, "workflow_id", f"$.automations[{index}]", issues)
			_required_string(item, "trigger", f"$.automations[{index}]", issues)
			_reference(item, "workflow_id", ids["workflows"], f"$.automations[{index}]", issues)
	for index, item in enumerate(graph.get("schedules", []) if isinstance(graph.get("schedules"), list) else []):
		if isinstance(item, Mapping):
			path = f"$.schedules[{index}]"
			for key in ("automation_id", "cron", "timezone"):
				_required_string(item, key, path, issues)
			if not isinstance(item.get("enabled"), bool):
				issues.append(ValidationIssue(f"{path}.enabled", "must be a boolean"))
			_reference(item, "automation_id", ids["automations"], f"$.schedules[{index}]", issues)
	for index, item in enumerate(graph.get("connectors", []) if isinstance(graph.get("connectors"), list) else []):
		if isinstance(item, Mapping):
			path = f"$.connectors[{index}]"
			_required_string(item, "type", path, issues)
			_string_list(item, "operations", path, issues, allow_empty=False)
	for index, item in enumerate(graph.get("permissions", []) if isinstance(graph.get("permissions"), list) else []):
		if isinstance(item, Mapping):
			path = f"$.permissions[{index}]"
			for key in ("principals", "actions", "resources"):
				_string_list(item, key, path, issues, allow_empty=False)
	for index, item in enumerate(graph.get("approval_rules", []) if isinstance(graph.get("approval_rules"), list) else []):
		if isinstance(item, Mapping):
			path = f"$.approval_rules[{index}]"
			if not isinstance(item.get("required"), bool):
				issues.append(ValidationIssue(f"{path}.required", "must be a boolean"))
			count = item.get("approvals_required")
			if isinstance(count, bool) or not isinstance(count, int) or count < 0:
				issues.append(ValidationIssue(f"{path}.approvals_required", "must be a non-negative integer"))
			_string_list(item, "actions", path, issues, allow_empty=False)

	for index, agent in enumerate(graph.get("agents", []) if isinstance(graph.get("agents"), list) else []):
		if not isinstance(agent, Mapping):
			continue
		path = f"$.agents[{index}]"
		actor_value = agent.get("actor_type")
		if actor_value not in {"builder_agent", "runtime_agent"}:
			issues.append(ValidationIssue(f"{path}.actor_type", "must be 'builder_agent' or 'runtime_agent'"))
		_string_list(agent, "capabilities", path, issues)
		if "tools" in agent:
			_string_list(agent, "tools", path, issues)
		actor_type = agent.get("actor_type", agent.get("role", "runtime_agent"))
		if actor_type != "runtime_agent":
			continue
		capabilities = _string_values(agent.get("capabilities", [])) + _string_values(agent.get("tools", []))
		for capability in capabilities:
			normalized = capability.strip().lower().replace("-", "_").replace(" ", "_")
			if normalized in _SOURCE_WRITE_CAPABILITIES or ("source" in normalized and "write" in normalized):
				issues.append(
					ValidationIssue(
						f"$.agents[{index}].capabilities",
						f"runtime agents may not receive source-code write capability {capability!r}",
					)
				)

	if issues:
		raise GraphValidationError(issues)
	return copy.deepcopy(dict(graph))
