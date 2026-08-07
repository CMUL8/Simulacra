"""Governance overview for IT control plane."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .runs import list_projects, project_dir


def governance_overview() -> dict[str, Any]:
	projects = list_projects()
	items: list[dict[str, Any]] = []
	pass_count = fail_count = deployed_count = plan_count = 0

	for p in projects:
		audit = _load_audit(p.id)
		gates = audit.get("gates", {})
		gate_status = gates.get("status", p.gates_status)
		if gate_status == "pass":
			pass_count += 1
		elif gate_status == "fail":
			fail_count += 1
		if p.deployed:
			deployed_count += 1
		if p.phase == "plan":
			plan_count += 1

		items.append(
			{
				"id": p.id,
				"title": p.app_config.title,
				"phase": p.phase,
				"status": p.status,
				"gates_status": gate_status,
				"deployed": p.deployed,
				"row_count": p.row_count,
				"plan_approved": p.plan_approved,
				"checkpoints": len(p.checkpoints),
				"created_at": p.created_at,
				"integration": {
					"layer": "simulacra-control-plane",
					"direct_access": False,
					"audit_logged": True,
				},
				"gates": gates.get("results", []),
				"deploy": audit.get("deploy"),
			}
		)

	return {
		"policy": {
			"direct_system_access": False,
			"message": "Apps never access business systems directly.",
			"description": (
				"Simulacra is your integration control layer — enforcing authentication, "
				"abstracting secrets, and auditing every data interaction."
			),
		},
		"summary": {
			"total_projects": len(items),
			"gates_pass": pass_count,
			"gates_fail": fail_count,
			"deployed": deployed_count,
			"in_plan": plan_count,
		},
		"projects": items,
	}


def _load_audit(project_id: str) -> dict[str, Any]:
	root = project_dir(project_id) / "audit"
	out: dict[str, Any] = {}
	for name in ("gates.json", "deploy.json"):
		path = root / name
		if path.exists():
			out[name.replace(".json", "")] = json.loads(path.read_text())
	return out
