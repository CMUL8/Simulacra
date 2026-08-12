"""Keep agent replies user-facing — no file manifests or path dumps."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

_FILE_EXT = r"(?:json|md|csv|tsv|txt|pdf|parquet|tsx?|jsx?|py)"
_FILE_TOKEN = re.compile(rf"`?[\w./\-]+\.{_FILE_EXT}`?", re.I)
_TABLE_ROW = re.compile(r"^\s*\|.+\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*[-:]+")
_MANIFEST_HEAD = re.compile(
	r"(?i)^\s{0,3}#{1,3}\s*(what.?s in (the )?data room|data room|sources?|files?)\b"
)
_VENDOR_LEADERBOARD = re.compile(r"(?i)vendor\s+leaderboard")


def _human_file_label(token: str) -> str:
	raw = token.strip("`")
	base = PurePosixPath(raw).name
	stem = PurePosixPath(base).stem
	stem = re.sub(r"^\d+[_\-\s]*", "", stem)
	stem = stem.replace("_", " ").replace("-", " ").strip()
	return stem or "source"


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
		# Drop "### What's in the data room" + following pipe table
		if _MANIFEST_HEAD.match(line):
			dropped_manifest = True
			i += 1
			while i < len(lines) and not lines[i].strip():
				i += 1
			while i < len(lines) and (_TABLE_ROW.match(lines[i]) or _TABLE_SEP.match(lines[i])):
				i += 1
			continue
		# Standalone pipe table that looks like a file list
		if _TABLE_ROW.match(line):
			block = [line]
			j = i + 1
			while j < len(lines) and (
				_TABLE_ROW.match(lines[j]) or _TABLE_SEP.match(lines[j]) or not lines[j].strip()
			):
				if lines[j].strip():
					block.append(lines[j])
				j += 1
			blob = "\n".join(block)
			if _FILE_TOKEN.search(blob) or re.search(r"(?i)\bfile\b", blob):
				dropped_manifest = True
				i = j
				continue
			out.extend(block)
			i = j
			continue
		soft = _FILE_TOKEN.sub(lambda m: _human_file_label(m.group(0)), line)
		soft = _VENDOR_LEADERBOARD.sub("Leadership view", soft)
		# Soften "I compiled … in `work/research/`" path brags
		soft = re.sub(
			r"(?i)\bacross\s+\d+\s+files?\s+in\s+`?work/research/?`?",
			"across your sources",
			soft,
		)
		soft = re.sub(r"(?i)\bin\s+`?work/research/?`?", "in the data room", soft)
		out.append(soft)
		i += 1

	text_out = "\n".join(out)
	text_out = re.sub(r"\n{3,}", "\n\n", text_out).strip()
	if dropped_manifest and "data room" not in text_out.lower():
		text_out = (
			f"{text_out}\n\nSources are in the data room." if text_out else "Sources are in the data room."
		)
	return text_out
