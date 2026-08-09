"""Soft source-room inventory for plan UI / Prime — not a build gate."""

from simulacra.demo.sources import source_room_brief, source_room_lines


def test_empty_room_brief():
	brief = source_room_brief({"row_count": 0, "files": []})
	assert brief["empty"] is True
	assert source_room_lines(brief) == ["No sources attached yet"]


def test_vendor_sample_flagged_but_not_blocking():
	brief = source_room_brief(
		{
			"row_count": 39,
			"vendors": ["Helios Analytics"],
			"files": [
				{"name": "notes.json"},
				{"name": "supplement.csv"},
				{"name": "vendor-research.md"},
			],
		}
	)
	assert brief["empty"] is False
	assert brief["looks_like_vendor_sample"] is True
	lines = source_room_lines(brief)
	assert any("vendor-risk sample" in ln for ln in lines)
