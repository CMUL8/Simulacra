from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .runs import project_dir


def rows_to_parquet(rows: list[dict[str, Any]], path: Path) -> None:
	path.parent.mkdir(parents=True, exist_ok=True)
	if not rows:
		# Truly empty — no forged vendor/risk sentinel row
		table = pa.table({"_empty": pa.array([], type=pa.string())})
		pq.write_table(table, path)
		return
	table = pa.Table.from_pylist(rows)
	pq.write_table(table, path)


def load_duckdb(project_id: str) -> duckdb.DuckDBPyConnection:
	root = project_dir(project_id)
	parquet = root / "outputs" / "table.parquet"
	con = duckdb.connect(str(root / "work" / "analytics.duckdb"))
	con.execute("CREATE OR REPLACE TABLE findings AS SELECT * FROM read_parquet(?)", [str(parquet)])
	return con


def query(project_id: str, sql: str) -> dict[str, Any]:
	con = load_duckdb(project_id)
	try:
		result = con.execute(sql)
		cols = [d[0] for d in result.description]
		rows = [dict(zip(cols, row)) for row in result.fetchall()]
		return {"columns": cols, "rows": rows, "row_count": len(rows)}
	finally:
		con.close()


def default_preview_query(project_id: str) -> dict[str, Any]:
	# No assumed risk_score column — generic preview
	return query(project_id, "SELECT * FROM findings LIMIT 100")
