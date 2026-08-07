from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .runs import project_dir


def rows_to_parquet(rows: list[dict[str, Any]], path: Path) -> None:
	if not rows:
		rows = [
			{
				"vendor": "none",
				"theme": "empty",
				"risk_level": "low",
				"risk_score": 0,
				"evidence": "No extractable findings in data room",
				"source_file": "",
			}
		]
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
	return query(project_id, "SELECT * FROM findings ORDER BY risk_score DESC LIMIT 100")
