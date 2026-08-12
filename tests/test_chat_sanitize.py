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
	assert "|" not in out
	assert "###" not in out
	assert "work/research" not in out.lower() or "data room" in out.lower()
	assert "Leadership view" in out
	assert "Vendor leaderboard" not in out
	assert "In the preview" in out
	assert "data room" in out.lower()


def test_orphan_pipe_row_dropped_when_file() -> None:
	out = sanitize_agent_reply(
		"| 00_summary.md | Narrative overview covering origins |"
	)
	assert "|" not in out
	assert ".md" not in out


def test_softens_inline_filenames() -> None:
	out = sanitize_agent_reply("See `03_ideology.json` for pillars.")
	assert ".json" not in out
	assert "ideology" in out.lower()


def test_strips_code_filenames_from_change_summary() -> None:
	raw = (
		"**What changed**\n"
		"- Title & config\n"
		"- Layout / UI (`App.tsx`)\n"
		"- Styles (`styles.css`)\n"
	)
	out = sanitize_agent_reply(raw)
	assert "App.tsx" not in out
	assert "styles.css" not in out
	assert "Layout & structure" in out
	assert "Title & framing" in out
	assert "Visual styling" in out or "Styles" not in out or "(" not in out
