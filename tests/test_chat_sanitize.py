"""Agent reply sanitization — no file manifests in chat."""

from __future__ import annotations

from simulacra.demo.chat_sanitize import sanitize_agent_reply


def test_strips_data_room_file_table() -> None:
	raw = (
		"I compiled research across 6 files in `work/research/`.\n\n"
		"### What's in the data room now\n"
		"| File | Contents |\n"
		"|------|----------|\n"
		"| `01_timeline.json` | events |\n"
		"| `02_leadership.json` | profiles |\n\n"
		"### What the app can show\n"
		"- KPI strip\n"
		"- Vendor leaderboard: leaders\n"
	)
	out = sanitize_agent_reply(raw)
	assert "01_timeline.json" not in out
	assert "work/research" not in out.lower() or "data room" in out.lower()
	assert "Leadership view" in out
	assert "Vendor leaderboard" not in out
	assert "data room" in out.lower()


def test_softens_inline_filenames() -> None:
	out = sanitize_agent_reply("See `03_ideology.json` for pillars.")
	assert ".json" not in out
	assert "ideology" in out.lower()
