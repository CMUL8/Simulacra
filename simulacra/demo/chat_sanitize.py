"""Keep agent replies user-facing — no file manifests or path dumps."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_FILE_EXT = r"(?:json|md|csv|tsv|txt|pdf|parquet|tsx?|jsx?|css|py)"
_FILE_TOKEN = re.compile(rf"`?[\w./\-]+\.{_FILE_EXT}`?", re.I)
_CODE_FILE_PAREN = re.compile(
	r"\s*\((?:`?(?:src/)?(?:App\.tsx|styles\.css|main\.tsx|index\.[jt]sx?)`?|`?[\w./\-]+\.(?:tsx?|jsx?|css|py)`?)\)",
	re.I,
)
_CODE_FILE_BARE = re.compile(
	r"(?i)\b(?:src/)?(?:App\.tsx|styles\.css|main\.tsx)\b",
)
_TABLE_ROW = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*[-:| ]+\|?\s*$")
_MANIFEST_HEAD = re.compile(
	r"(?i)^\s{0,3}#{0,3}\s*(what.?s in (the )?data room|data room|sources?|files?|inventory)\b"
)
_ADDED_SOURCES_LINE = re.compile(
	r"(?i)^\s*added\b.+\bto (your |the )?(sources?|data room)\b\.?\s*$"
)
_SOURCES_IN_ROOM = re.compile(
	r"(?i)^\s*sources are in the data room\.?\s*$"
)
_INTERNAL_SOURCE_NAMES = re.compile(
	r"(?i)\b(design_brief|kernel-state|kernel_state|agent_context|plan_preview|"
	r"extract_report|gates?_report|run_manifest|data_profile|sources)\.(json|md)\b"
)
_CHOICE_DUMP = re.compile(
	r"(?is)(what I'?d do differently|want me to try|targeted iterates?|"
	r"rebuild (fresh )?from scratch|either way gets you|"
	r"break the (visual|content) refresh into smaller|"
	r"content rewrites? stalled|bundled too many|content layer timed out|"
	r"styling layer.{0,40}landed fine)"
)
_APP_SHOW_HEAD = re.compile(
	r"(?i)^\s{0,3}#{0,3}\s*what the (app|report|deck|artifact) can show\b"
)
_WIDGET_BULLET = re.compile(
	r"(?i)^\s*[-*+]\s*(?:\*\*)?(kpi|chart|tables?|findings|leaderboard|empty state|"
	r"dashboard|timeline|scorecard|strip|map|filters?|vendor)\b"
)
_PREVIEW_HOLDS = "The preview holds the layout — charts, tables, and empty states live there."
_HIT_BUILD = re.compile(
	r"(?i)(?:one\s+click\s+on|click\s+on|(?:hit|press|click|tap)\s+)\s*\*{0,2}build\*{0,2}"
)
_ASKS_BUILD = re.compile(
	r"(?i)("
	r"(?:one\s+click\s+on|click\s+on|(?:hit|press|click|tap)\s+)\s*\*{0,2}build\*{0,2}"
	r"|confirm below"
	r"|give the go-ahead"
	r"|ready when you are"
	r"|whenever you say go"
	r")"
)
_REBUILD_DRAFT = re.compile(r"(?i)\*{0,2}Rebuild from draft\*{0,2}")
_RETRY_BUILD = re.compile(
	r"(?i)retry\s+\*{0,2}Build(?:\s+(?:app|report|slides|one-pager))?\*{0,2}"
)
_BUILD_FIRST = re.compile(r"(?i)_\(Build first — then I can apply edits\.\)_")
_APPROVE_AGAIN = re.compile(r"(?i)you can refine or Approve again\.?")
_BUILD_COMPLETE_PREVIEW = re.compile(
	r"(?i)\bBuild complete\s*[—–-]\s*open\s+\*{0,2}Preview\*{0,2}\s+to review\.?"
)
_READY_CONFIRM = re.compile(
	r"(?i)when you['’]re ready,\s*confirm below\.?"
)
_VENDOR_KPI_LINE = re.compile(
	r"(?i)^\s*(?:\*{0,2}Sources?:\*{0,2}\s*)?\d+\s+rows(?:\s*[·•,]\s*\d+\s+high(?:\s+risk)?)?(?:\s*[·•,]\s*\d+\s+vendors?)?\s*$"
)
_FILENAME_ONLY_LINE = re.compile(
	r"(?i)^\s*(?:`?[\w.-]+\.(?:json|csv|md|tsv|txt)`?(?:\s*[,·•]\s*)?){2,}\s*$"
)
_SAVED_TO_FILE = re.compile(
	r"(?i)^\s*all saved to `?[\w./-]+\.(?:json|md|csv)`?\.?\s*$"
)
_CRAFT_FALLBACK = re.compile(
	r"(?i)\s*\(craft fallback[^)]*\)|\bagent file edits incomplete\.?"
)
_STYLE_BRIEF = re.compile(
	r"(?i)layout was personalized from your style brief[^.]*\."
)
_HITTING_ITERATE = re.compile(r"(?i)\bhitting iterate\b")
_SERPER_ASIDE = re.compile(r"(?i)\s*\(since serper web search isn't configured\)")
_WHAT_CHANGED_HEAD = re.compile(r"(?i)^\s{0,3}(\*{0,2}|#{1,3}\s*)what changed\b")
_INVENTORY_BULLET = re.compile(
	r"(?i)^\s*[-*]\s*(title\s*&\s*(config|framing)|layout\s*(/\s*ui|&\s*structure)|"
	r"styles?(?:\s*\(.*\))?|visual styling|content update)\b"
)
_VENDOR_LEADERBOARD = re.compile(r"(?i)vendor\s+leaderboard")
_HASH_HEAD = re.compile(r"^\s{0,3}(#{1,3})\s+(.+)$")


def _human_file_label(token: str) -> str:
	raw = token.strip("`")
	base = PurePosixPath(raw).name
	stem = PurePosixPath(base).stem
	stem = re.sub(r"^\d+[_\-\s]*", "", stem)
	stem = stem.replace("_", " ").replace("-", " ").strip()
	return stem[:1].upper() + stem[1:] if stem else "Source"


def _cells(line: str) -> list[str]:
	inner = line.strip().strip("|")
	return [c.strip().strip("`") for c in inner.split("|")]


def _table_to_bullets(block_lines: list[str]) -> list[str]:
	"""Turn a markdown pipe table into clean bullets — never keep pipes."""
	rows: list[list[str]] = []
	for line in block_lines:
		if _TABLE_SEP.match(line):
			continue
		if not _TABLE_ROW.match(line):
			continue
		cells = _cells(line)
		if not cells:
			continue
		joined = " ".join(cells).lower()
		if joined in ("file contents", "files contents", "name description", "source contents"):
			continue
		if cells[0].lower() in ("file", "files", "name", "source", "path"):
			continue
		rows.append(cells)

	if not rows:
		return []

	fileish = sum(
		1 for r in rows if _FILE_TOKEN.search(r[0]) or re.search(rf"\.{_FILE_EXT}$", r[0], re.I)
	)
	# File inventory → drop; data room owns this
	if fileish >= max(1, len(rows) // 2):
		return []

	bullets: list[str] = []
	for cells in rows:
		left = _FILE_TOKEN.sub(lambda m: _human_file_label(m.group(0)), cells[0])
		left = _human_file_label(left) if re.search(rf"\.{_FILE_EXT}$", left, re.I) else left
		right = " — ".join(c for c in cells[1:] if c).strip()
		right = _VENDOR_LEADERBOARD.sub("Leadership view", right)
		if right:
			bullets.append(f"- **{left}** — {right}")
		else:
			bullets.append(f"- {left}")
	return bullets


def reply_asks_to_build(text: str) -> bool:
	"""True when the agent is waiting for the user to confirm scaffold."""
	return bool(text and _ASKS_BUILD.search(text))


def _rewrite_control_ctas(text: str) -> str:
	"""Chat may only name controls that exist: Confirm below, Preview, Ship, Start over."""
	text = _HIT_BUILD.sub("Confirm below", text)
	text = _REBUILD_DRAFT.sub("**Start over**", text)
	text = _RETRY_BUILD.sub("use **Start over**", text)
	text = _BUILD_FIRST.sub("Confirm below first — then I can apply edits.", text)
	text = _APPROVE_AGAIN.sub("You can refine in chat, or Start over.", text)
	text = _BUILD_COMPLETE_PREVIEW.sub("It's in Preview.", text)
	text = _READY_CONFIRM.sub("Confirm below when you’re ready.", text)
	text = _HITTING_ITERATE.sub("Updating", text)
	text = _SERPER_ASIDE.sub("", text)
	text = _CRAFT_FALLBACK.sub("", text)
	text = _STYLE_BRIEF.sub("Preview is ready.", text)
	return text


def sanitize_agent_reply(text: str) -> str:
	"""Strip markdown file tables and soften path-heavy copy for chat."""
	if not text or not text.strip():
		return text
	# Kill "split vs rebuild" lectures — product should just iterate
	if _CHOICE_DUMP.search(text):
		return "Updating the layout now — charts, stats, and structure in one pass."
	lines = text.replace("\r\n", "\n").split("\n")
	out: list[str] = []
	i = 0
	while i < len(lines):
		line = lines[i]
		if _MANIFEST_HEAD.match(line):
			i += 1
			while i < len(lines) and not lines[i].strip():
				i += 1
			while i < len(lines) and (_TABLE_ROW.match(lines[i]) or _TABLE_SEP.match(lines[i])):
				i += 1
			continue
		if _ADDED_SOURCES_LINE.match(line) or _SOURCES_IN_ROOM.match(line):
			# Quiet inventory — never echo filename dumps / filler into chat
			i += 1
			continue
		if _VENDOR_KPI_LINE.match(line) or _FILENAME_ONLY_LINE.match(line) or _SAVED_TO_FILE.match(line):
			i += 1
			continue
		if _INTERNAL_SOURCE_NAMES.search(line) and re.search(
			r"(?i)\b(added|source|data room|promoted|inventory)\b", line
		):
			i += 1
			continue
		if _APP_SHOW_HEAD.match(line):
			i += 1
			while i < len(lines) and (not lines[i].strip() or _WIDGET_BULLET.match(lines[i]) or lines[i].lstrip().startswith(("- ", "* "))):
				i += 1
			out.append("")
			out.append(_PREVIEW_HOLDS)
			out.append("")
			continue
		if _WIDGET_BULLET.match(line):
			block = [line]
			j = i + 1
			while j < len(lines) and (not lines[j].strip() or _WIDGET_BULLET.match(lines[j])):
				if lines[j].strip():
					block.append(lines[j])
				j += 1
			if len(block) >= 3:
				out.append("")
				out.append(_PREVIEW_HOLDS)
				out.append("")
				i = j
				continue
		if _WHAT_CHANGED_HEAD.match(line):
			i += 1
			while i < len(lines) and (
				not lines[i].strip() or _INVENTORY_BULLET.match(lines[i]) or _CODE_FILE_PAREN.search(lines[i])
				or _CODE_FILE_BARE.search(lines[i])
				or re.match(r"(?i)^\s*[-*]\s*(request:|layout|title|styles?|visual|content)\b", lines[i])
			):
				i += 1
			continue
		if _INVENTORY_BULLET.match(line):
			i += 1
			continue
		m_head = _HASH_HEAD.match(line)
		if m_head:
			title = m_head.group(2).strip()
			title = _FILE_TOKEN.sub(lambda m: _human_file_label(m.group(0)), title)
			out.append("")
			out.append(title)
			i += 1
			continue
		if _TABLE_ROW.match(line):
			block = [line]
			j = i + 1
			while j < len(lines) and (
				_TABLE_ROW.match(lines[j]) or _TABLE_SEP.match(lines[j]) or not lines[j].strip()
			):
				if lines[j].strip():
					block.append(lines[j])
				j += 1
			bullets = _table_to_bullets(block)
			if bullets:
				out.append("")
				out.extend(bullets)
				out.append("")
			i = j
			continue
		# Drop code paths before softening data-room filenames
		soft = _CODE_FILE_PAREN.sub("", line)
		soft = _CODE_FILE_BARE.sub("", soft)
		soft = re.sub(r"(?i)\blayout\s*/\s*ui\b", "Layout & structure", soft)
		soft = re.sub(r"(?i)\btitle\s*&\s*config\b", "Title & framing", soft)
		soft = re.sub(r"(?i)^(\s*[-*]\s*)styles\b.*$", r"\1Visual styling", soft)
		soft = re.sub(r"(?i)^(\s*[-*]\s*)layout\s*&\s*structure\b.*$", r"\1Layout & structure", soft)
		soft = _FILE_TOKEN.sub(lambda m: _human_file_label(m.group(0)), soft)
		soft = re.sub(r"\s*\((?:App|Styles)\)\s*$", "", soft)
		soft = _VENDOR_LEADERBOARD.sub("Leadership view", soft)
		soft = re.sub(
			r"(?i)\bacross\s+\d+\s+files?\s+in\s+`?work/research/?`?",
			"across your sources",
			soft,
		)
		soft = re.sub(r"(?i)\bin\s+`?work/research/?`?", "in the data room", soft)
		if "|" in soft and soft.count("|") >= 2 and soft.strip().startswith("|"):
			cells = _cells(soft)
			if cells:
				if _FILE_TOKEN.search(cells[0]) or re.search(rf"\.{_FILE_EXT}$", cells[0], re.I):
					i += 1
					continue
				left = _human_file_label(cells[0]) if cells[0] else ""
				right = " — ".join(c for c in cells[1:] if c)
				soft = f"- **{left}** — {right}" if right else f"- {left}"
		out.append(soft)
		i += 1

	text_out = "\n".join(out)
	text_out = _rewrite_control_ctas(text_out)
	text_out = re.sub(r"\n{3,}", "\n\n", text_out).strip()
	return text_out