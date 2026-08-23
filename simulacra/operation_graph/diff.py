from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .validation import AREAS, validate_operation_graph


@dataclass(frozen=True)
class StructuralDiff:
	added: tuple[str, ...]
	changed: tuple[str, ...]
	removed: tuple[str, ...]
	security_impact: tuple[str, ...]
	migration_impact: tuple[str, ...]
	test_impact: tuple[str, ...]

	@property
	def is_empty(self) -> bool:
		return not (self.added or self.changed or self.removed)

	def to_dict(self) -> dict[str, list[str]]:
		return {
			"added": list(self.added),
			"changed": list(self.changed),
			"removed": list(self.removed),
			"security_impact": list(self.security_impact),
			"migration_impact": list(self.migration_impact),
			"test_impact": list(self.test_impact),
		}


def _walk(old: Any, new: Any, path: str, buckets: dict[str, list[str]]) -> None:
	if isinstance(old, Mapping) and isinstance(new, Mapping):
		for key in sorted(old.keys() - new.keys()):
			buckets["removed"].append(f"{path}.{key}")
		for key in sorted(new.keys() - old.keys()):
			buckets["added"].append(f"{path}.{key}")
		for key in sorted(old.keys() & new.keys()):
			_walk(old[key], new[key], f"{path}.{key}", buckets)
		return
	if isinstance(old, list) and isinstance(new, list):
		old_named = all(isinstance(item, Mapping) and isinstance(item.get("id"), str) for item in old)
		new_named = all(isinstance(item, Mapping) and isinstance(item.get("id"), str) for item in new)
		if old_named and new_named:
			old_map = {item["id"]: item for item in old}
			new_map = {item["id"]: item for item in new}
			for item_id in sorted(old_map.keys() - new_map.keys()):
				buckets["removed"].append(f"{path}[id={item_id}]")
			for item_id in sorted(new_map.keys() - old_map.keys()):
				buckets["added"].append(f"{path}[id={item_id}]")
			for item_id in sorted(old_map.keys() & new_map.keys()):
				_walk(old_map[item_id], new_map[item_id], f"{path}[id={item_id}]", buckets)
		elif old != new:
			buckets["changed"].append(path)
		return
	if old != new:
		buckets["changed"].append(path)


def structural_diff(old_graph: Mapping[str, Any], new_graph: Mapping[str, Any]) -> StructuralDiff:
	old = validate_operation_graph(old_graph)
	new = validate_operation_graph(new_graph)
	buckets: dict[str, list[str]] = {"added": [], "changed": [], "removed": []}
	_walk(old, new, "$", buckets)
	paths = tuple(sorted({*buckets["added"], *buckets["changed"], *buckets["removed"]}))
	security_areas = ("$.agents", "$.connectors", "$.permissions", "$.approval_rules")
	migration_areas = ("$.entities", "$.workflows")
	security = tuple(f"Review authorization and external-action exposure for {path}" for path in paths if path.startswith(security_areas))
	migration = tuple(f"Assess persisted-data or workflow-state migration for {path}" for path in paths if path.startswith(migration_areas))
	touched_areas = sorted({path.split(".", 2)[1].split("[", 1)[0] for path in paths if path.startswith("$.")})
	tests = tuple(f"Exercise Operation Graph {area} contract and affected runtime consumer" for area in touched_areas)
	return StructuralDiff(
		added=tuple(buckets["added"]),
		changed=tuple(buckets["changed"]),
		removed=tuple(buckets["removed"]),
		security_impact=security,
		migration_impact=migration,
		test_impact=tests,
	)
