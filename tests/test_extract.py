from __future__ import annotations

from pathlib import Path

from simulacra.demo.extract import extract_data_room, is_diligence_rows, write_summary
from simulacra.demo.paths import FIXTURES


def test_fixture_extracts_rows() -> None:
	rows = extract_data_room(FIXTURES)
	assert len(rows) >= 5
	# Structured CSV/JSON fixtures keep their native vendor/risk schema
	vendors = {r.get("vendor") for r in rows if r.get("vendor")}
	assert "Cobalt Security" in vendors or "Summit Retail" in vendors
	assert any(r.get("risk_level") == "high" for r in rows if "risk_level" in r)
	assert is_diligence_rows(rows)


def test_markdown_parser_is_topic_neutral() -> None:
	from simulacra.demo.extract import _parse_markdown_block

	text = "# TestCo\n- critical breach in audit\n"
	rows = _parse_markdown_block(text, "t.md")
	assert rows[0]["heading"] == "TestCo"
	assert "critical breach" in rows[0]["text"]
	assert "risk_level" not in rows[0]
	assert "vendor" not in rows[0]
	assert not is_diligence_rows(rows)


def test_write_summary_neutral_for_generic_rows() -> None:
	summary = write_summary([{"year": 2014, "seats": 44, "party": "INC"}], "Congress report")
	assert "Vendors:" not in summary
	assert "**Rows:** 1" in summary


def test_csv_passthrough_preserves_columns(tmp_path: Path) -> None:
	path = tmp_path / "votes.csv"
	path.write_text("year,seats,party\n2014,44,INC\n2019,52,INC\n")
	rows = extract_data_room(tmp_path)
	assert rows[0]["year"] == 2014
	assert rows[0]["party"] == "INC"
	assert "vendor" not in rows[0]
	assert not is_diligence_rows(rows)


def test_pseudo_diligence_unwrapped() -> None:
	from simulacra.demo.extract import normalize_extracted_rows

	poison = [
		{
			"vendor": "unknown",
			"theme": "Congress won only 44 seats",
			"risk_level": "medium",
			"risk_score": 50,
			"evidence": '{"finding":"Congress won only 44 seats","year":"2014","category":"Lok Sabha","metric":"seats_won","value":"44","comparison":"vs 206","severity":"high"}',
			"source_file": "findings.csv",
		}
	]
	assert not is_diligence_rows(poison)
	clean = normalize_extracted_rows(poison)
	assert clean[0]["finding"].startswith("Congress")
	assert "risk_score" not in clean[0]
	assert "vendor" not in clean[0]
