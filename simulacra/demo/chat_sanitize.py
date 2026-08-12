"""Keep agent replies user-facing — no file manifests or path dumps."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_FILE_EXT = r"(?:json|md|csv|tsv|txt|pdf|parquet|tsx?|jsx?|py)"
_FILE_TOKEN = re.compile(rf"`?[\w./\-]+\.{_FILE_EXT}`?", re.I)
_TABLE_ROW = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*[-:| ]+\|?\s*$")
_MANIFEST_HEAD = re.compile(
	r"(?i)^\s{0,3}#{1,3}\s*(what.?s in (the )?data room|data room|sources?|files?|inventory)\b"
)
_ADDED_SOURCES_LINE = re.compile(
	r"(?i)^\s*added\b.+\bto (your |the )?sources?\b\.?\s*$"
)
_APP_SHOW_HEAD = re.compile(
	r"(?i)^\s{0,3}#{1,3}\s*what the (app|report|deck|artifact) can show\b"
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


def sanitize_agent_reply(text: str) -> str:
	"""Strip markdown file tables and soften path-heavy copy for chat."""
	if not text or not text.strip():
		return text
	lines = text.replace("\r\n", "\n").split("\n")
	out: list[str] = []
	i = 0
	dropped_manifest = False
	while i < len(lines):
		line = lines[i]
		if _MANIFEST_HEAD.match(line):
			dropped_manifest = True
			i += 1
			while i < len(lines) and not lines[i].strip():
				i += 1
			while i < len(lines) and (_TABLE_ROW.match(lines[i]) or _TABLE_SEP.match(lines[i])):
				i += 1
			continue
		if _ADDED_SOURCES_LINE.match(line):
			# Quiet inventory — never echo filename dumps into chat
			i += 1
			continue
		if _APP_SHOW_HEAD.match(line):
			out.append("")
			out.append("In the preview")
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
			if not bullets:
				dropped_manifest = True
			else:
				out.append("")
				out.extend(bullets)
				out.append("")
			i = j
			continue
		soft = _FILE_TOKEN.sub(lambda m: _human_file_label(m.group(0)), line)
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
					dropped_manifest = True
					i += 1
					continue
				left = _human_file_label(cells[0]) if cells[0] else ""
				right = " — ".join(c for c in cells[1:] if c)
				soft = f"- **{left}** — {right}" if right else f"- {left}"
		out.append(soft)
		i += 1

	text_out = "\n".join(out)
	text_out = re.sub(r"\n{3,}", "\n\n", text_out).strip()
	if dropped_manifest and "data room" not in text_out.lower():
		text_out = (
			f"{text_out}\n\nSources are in the data room." if text_out else "Sources are in the data room."
		)
	return text_out
