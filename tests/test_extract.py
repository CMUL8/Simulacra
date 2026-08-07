from __future__ import annotations

from pathlib import Path

from simulacra.demo.extract import extract_data_room
from simulacra.demo.paths import FIXTURES


def test_fixture_extracts_rows() -> None:
	rows = extract_data_room(FIXTURES)
	assert len(rows) >= 5
	vendors = {r["vendor"] for r in rows}
	assert "Helios Analytics" in vendors
	assert any(r["risk_level"] == "high" for r in rows)


def test_markdown_parser_finds_vendor() -> None:
	from simulacra.demo.extract import _parse_markdown_block

	text = "# TestCo\n- critical breach in audit\n"
	rows = _parse_markdown_block(text, "t.md")
	assert rows[0]["vendor"] == "TestCo"
	assert rows[0]["risk_level"] == "high"
