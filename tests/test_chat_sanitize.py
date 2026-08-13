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
	assert "work/research" not in out.lower()
	assert "preview holds the layout" in out.lower()
	assert "Vendor leaderboard" not in out
	assert "In the preview" not in out
	assert "What's in the data room" not in out
	assert "Sources are in the data room" not in out
	assert "KPI strip" not in out


def test_no_filler_sources_line() -> None:
	out = sanitize_agent_reply(
		"Research done.\n\nSources are in the data room.\n\nHit Build when ready."
	)
	assert "Sources are in the data room" not in out
	assert "Hit Build" not in out
	assert "Confirm below" in out


def test_rewrites_hit_build_cta() -> None:
	from simulacra.demo.chat_sanitize import reply_asks_to_build

	raw = (
		"Hit Build when you're ready, and I'll scaffold the interactive report "
		"with the timeline, electoral charts, leadership profiles, and policy deep-dives."
	)
	assert reply_asks_to_build(raw)
	out = sanitize_agent_reply(raw)
	assert "Hit Build" not in out
	assert "Confirm below" in out
	assert "scaffold the interactive report" in out
	assert "Rebuild from draft" not in sanitize_agent_reply("Try **Rebuild from draft**.")
	assert "Start over" in sanitize_agent_reply("Try **Rebuild from draft**.")
	assert "Build app" not in sanitize_agent_reply("retry **Build app**")
	assert "Start over" in sanitize_agent_reply("retry **Build app**")
	click = sanitize_agent_reply("One click on Build and you'll see it. Ready when you are.")
	assert "click on Build" not in click
	assert "Confirm below" in click
	from simulacra.demo.chat_sanitize import reply_asks_to_build as asks
	assert asks("Ready when you are.")
	assert asks("I'll scaffold once you give the go-ahead.")
	assert not asks("Want me to go ahead with that research?")
	assert "bjp_research.json" not in sanitize_agent_reply("All saved to bjp_research.json.")
	assert "high risk" not in sanitize_agent_reply("Sources: 39 rows · 16 high risk · 22 vendors")
	assert "craft fallback" not in sanitize_agent_reply(
		"Built. Layout was personalized from your Style brief (craft fallback — agent file edits incomplete)."
	)
	done = sanitize_agent_reply(
		"Built **BJP History Report**. Build complete — open **Preview** to review."
	)
	assert "Hit Build" not in done
	assert "Build complete" not in done
	assert "Preview" in done


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


def test_collapses_widget_spec_list() -> None:
	out = sanitize_agent_reply(
		"I'll build this next.\n\n"
		"- KPI strip: seats and founding year\n"
		"- Findings table: chronological timeline\n"
		"- Chart: seat count across elections\n"
		"- Empty state: honest if sources aren't loaded yet\n"
	)
	assert "KPI strip" not in out
	assert "Findings table" not in out
	assert "preview holds the layout" in out.lower()
	assert "I'll build this next" in out
