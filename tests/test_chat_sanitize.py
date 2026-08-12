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


def test_strips_added_sources_inventory() -> None:
	out = sanitize_agent_reply(
		"Added `design_brief.json`, `kernel-state.json`, `00_summary.md` (+4 more) to your sources.\n\n"
		"Ready to build."
	)
	assert "design_brief" not in out.lower()
	assert "to your sources" not in out.lower()
	assert "Ready to build" in out


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
	assert "Title & config" not in out or "Title & framing" in out


def test_change_summary_has_no_file_inventory() -> None:
	from simulacra.demo.pipeline import _change_summary_lines, _honesty_change_note

	lines = _change_summary_lines(
		"denser layout",
		["src/App.tsx", "src/styles.css", "public/config.json"],
		layout=True,
	)
	joined = "\n".join(lines)
	assert "App.tsx" not in joined
	assert "styles.css" not in joined
	assert "Layout" not in joined
	assert "Title" not in joined
	assert "Request: denser layout" in joined
	note = _honesty_change_note("x", ["src/App.tsx"], layout=True)
	assert "What changed" not in note
	assert "App.tsx" not in note
