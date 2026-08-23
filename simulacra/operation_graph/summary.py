from __future__ import annotations

from typing import Any, Mapping

from .validation import AREAS, validate_operation_graph


def business_summary(graph: Mapping[str, Any]) -> str:
	validated = validate_operation_graph(graph)
	metadata = validated["metadata"]
	counts = ", ".join(f"{len(validated[area])} {area.replace('_', ' ')}" for area in AREAS)
	active_schedules = sum(1 for schedule in validated["schedules"] if schedule.get("enabled", True))
	consequential = sum(
		1
		for rule in validated["approval_rules"]
		if rule.get("required", True) or int(rule.get("approvals_required", 1)) > 0
	)
	return (
		f"{metadata['name']} (version {metadata['version']}) defines {counts}. "
		f"{active_schedules} schedules are active and {consequential} approval rules govern consequential work."
	)
