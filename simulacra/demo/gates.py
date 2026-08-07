from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .runs import ProjectState, file_hash, project_dir


def run_gates(project_id: str, *, min_rows: int = 1) -> dict[str, Any]:
	root = project_dir(project_id)
	manifest_path = root / "outputs" / "manifest.json"
	results: list[dict[str, Any]] = []

	def record(name: str, passed: bool, detail: str) -> None:
		results.append({"gate": name, "passed": passed, "detail": detail})

	# manifest_present
	if not manifest_path.exists():
		record("manifest_present", False, "manifest.json missing")
		status = "fail"
	else:
		record("manifest_present", True, "manifest.json found")
		manifest = json.loads(manifest_path.read_text())
		schema = manifest.get("artifacts", [{}])[0].get("schema", [])
		record("schema_match", bool(schema), f"{len(schema)} columns declared")

	# row_count_bounds
	parquet = root / "outputs" / "table.parquet"
	if parquet.exists():
		import pyarrow.parquet as pq

		n = pq.read_table(parquet).num_rows
		record("row_count_bounds", n >= min_rows, f"{n} rows (min {min_rows})")
	else:
		record("row_count_bounds", False, "table.parquet missing")

	# no_path_escape — outputs only under outputs/
	escaped = False
	for path in root.rglob("*"):
		if path.is_file() and path.suffix in {".parquet", ".json", ".md"}:
			rel = path.relative_to(root)
			parts = rel.parts
			if parts[0] not in ("outputs", "app", "audit", "work") and rel.name not in (
				"state.json",
				"simulacra.yaml",
			):
				if "inputs" not in parts:
					escaped = True
	record("no_path_escape", not escaped, "write jail ok" if not escaped else "unexpected writes")

	status = "pass" if all(r["passed"] for r in results) else "fail"
	audit = {
		"status": status,
		"results": results,
		"checked_at": datetime.now(UTC).isoformat(),
	}
	(root / "audit" / "gates.json").write_text(json.dumps(audit, indent=2))
	return audit


def write_manifest(
	state: ProjectState,
	rows: list[dict[str, Any]],
	sources: list[dict[str, str]],
	*,
	prime: dict[str, Any] | None = None,
) -> None:
	root = project_dir(state.id)
	parquet = root / "outputs" / "table.parquet"
	schema = []
	if rows:
		sample = rows[0]
		schema = [{"name": k, "type": "number" if isinstance(sample[k], (int, float)) else "string"} for k in sample]

	manifest = {
		"simulacra_version": "0.1.0",
		"run_id": state.id,
		"created_at": datetime.now(UTC).isoformat(),
		"task": state.prompt,
		"sources": sources,
		"artifacts": [
			{
				"path": "outputs/table.parquet",
				"kind": "table",
				"schema": schema,
				"row_count": len(rows),
				"content_hash": file_hash(parquet) if parquet.exists() else None,
			}
		],
		"gates": {"status": state.gates_status, "results": []},
		"prime": prime or {"session_id": None, "model": "simulacra-demo-pipeline", "source": "heuristic"},
		"design_brief": state.design_brief,
		"app": {"template": "internal-app", "path": "app"},
	}
	(root / "outputs" / "manifest.json").write_text(json.dumps(manifest, indent=2))
