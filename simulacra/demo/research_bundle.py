"""Bundle chat/agent research files into app/public/research.json for report preview."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .runs import project_dir

_SKIP_DIR_NAMES = frozenset(
	{"node_modules", ".git", "dist", ".venv", "__pycache__", ".next"}
)

_RESEARCH_NAME = re.compile(r"research|bjp", re.I)
_TOPIC_HINT = re.compile(
	r"\b(research|replace|rebuild|topic|rewrite|restyle|bjp)\b",
	re.I,
)


def message_suggests_research(message: str) -> bool:
	return bool(_TOPIC_HINT.search(message or ""))


def _should_skip_dir(path: Path) -> bool:
	return any(part in _SKIP_DIR_NAMES for part in path.parts)


def find_research_candidates(root: Path) -> list[Path]:
	"""Find research-like files under the project (excludes node_modules / .git)."""
	found: list[Path] = []
	if not root.is_dir():
		return found

	priority_dirs = [
		root / "work",
		root / "inputs" / "data-room",
		root,
	]
	seen: set[Path] = set()

	def _consider(path: Path) -> None:
		if not path.is_file():
			return
		try:
			resolved = path.resolve()
		except OSError:
			return
		if resolved in seen:
			return
		if _should_skip_dir(path.relative_to(root) if path.is_relative_to(root) else path):
			return
		# Prefer name matches; also recent md/json in work / data-room
		name = path.name
		rel = str(path.relative_to(root)) if path.is_relative_to(root) else name
		in_work = rel.startswith("work/") or "/work/" in rel
		in_room = "inputs/data-room" in rel.replace("\\", "/")
		ext = path.suffix.lower()
		if ext not in {".json", ".md", ".txt"}:
			return
		if _RESEARCH_NAME.search(name) or (in_work or in_room) and ext in {".md", ".json"}:
			# Skip the output bundle itself and huge binaries masquerading as text
			if name == "research.json" and "app/public" in rel.replace("\\", "/"):
				return
			seen.add(resolved)
			found.append(path)

	for base in priority_dirs:
		if not base.is_dir():
			continue
		for path in base.rglob("*"):
			if path.is_dir():
				if path.name in _SKIP_DIR_NAMES:
					continue
				continue
			if any(p in _SKIP_DIR_NAMES for p in path.parts):
				continue
			_consider(path)

	# Prefer *research* / bjp* names, then newest mtime
	def _rank(p: Path) -> tuple[int, float]:
		name_hit = 1 if _RESEARCH_NAME.search(p.name) else 0
		try:
			mtime = p.stat().st_mtime
		except OSError:
			mtime = 0.0
		return (name_hit, mtime)

	found.sort(key=_rank, reverse=True)
	return found


def _as_str(value: Any, limit: int = 4000) -> str:
	if value is None:
		return ""
	if isinstance(value, str):
		return value.strip()[:limit]
	if isinstance(value, (int, float, bool)):
		return str(value)
	if isinstance(value, list):
		parts = [_as_str(v, limit=500) for v in value[:40]]
		return "\n".join(p for p in parts if p)[:limit]
	if isinstance(value, dict):
		return json.dumps(value, indent=2)[:limit]
	return str(value)[:limit]


def _bullets_from(value: Any) -> list[str]:
	if isinstance(value, list):
		out: list[str] = []
		for item in value[:24]:
			text = _as_str(item, limit=400)
			if text:
				out.append(text)
		return out
	if isinstance(value, str) and value.strip():
		lines = [ln.strip(" -•\t") for ln in value.splitlines() if ln.strip()]
		return [ln[:400] for ln in lines[:24]]
	return []


def _section(heading: str, body: str = "", bullets: list[str] | None = None) -> dict[str, Any]:
	return {
		"heading": (heading or "Section").strip()[:160],
		"body": (body or "").strip()[:4000],
		"bullets": list(bullets or [])[:24],
	}


def parse_research_payload(raw: Any, *, fallback_title: str = "Research brief") -> dict[str, Any]:
	"""Normalize arbitrary JSON/dict research into the stable report shape."""
	if isinstance(raw, list):
		sections = []
		for i, item in enumerate(raw[:20], start=1):
			if isinstance(item, dict):
				heading = (
					_as_str(item.get("heading") or item.get("title") or item.get("name"), 160)
					or f"Section {i}"
				)
				body = _as_str(
					item.get("body")
					or item.get("text")
					or item.get("summary")
					or item.get("content"),
					4000,
				)
				bullets = _bullets_from(item.get("bullets") or item.get("points") or item.get("key_points"))
				sections.append(_section(heading, body, bullets))
			else:
				sections.append(_section(f"Point {i}", _as_str(item, 2000)))
		return {
			"title": fallback_title,
			"subtitle": "",
			"source_note": "Bundled from research list",
			"sections": sections,
		}

	if not isinstance(raw, dict):
		text = _as_str(raw, 6000)
		return {
			"title": fallback_title,
			"subtitle": "",
			"source_note": "",
			"sections": [_section("Overview", text)] if text else [],
		}

	title = _as_str(
		raw.get("title") or raw.get("name") or raw.get("topic") or fallback_title,
		120,
	) or fallback_title
	subtitle = _as_str(
		raw.get("subtitle") or raw.get("one_liner") or raw.get("summary") or raw.get("tagline"),
		240,
	)
	source_note = _as_str(
		raw.get("source_note") or raw.get("sources") or raw.get("attribution"),
		400,
	)

	sections: list[dict[str, Any]] = []
	raw_sections = raw.get("sections") or raw.get("chapters") or raw.get("parts")
	if isinstance(raw_sections, list) and raw_sections:
		for i, item in enumerate(raw_sections[:24], start=1):
			if isinstance(item, dict):
				heading = (
					_as_str(item.get("heading") or item.get("title") or item.get("name"), 160)
					or f"Section {i}"
				)
				body = _as_str(
					item.get("body")
					or item.get("text")
					or item.get("content")
					or item.get("summary"),
					4000,
				)
				bullets = _bullets_from(
					item.get("bullets") or item.get("points") or item.get("key_points")
				)
				sections.append(_section(heading, body, bullets))
			else:
				sections.append(_section(f"Section {i}", _as_str(item, 2000)))
	else:
		# Common research JSON shapes: findings / themes / key_points / overview
		overview = _as_str(raw.get("overview") or raw.get("executive_summary") or raw.get("intro"), 4000)
		if overview:
			sections.append(_section("Overview", overview))
		for key, label in (
			("findings", "Findings"),
			("key_findings", "Key findings"),
			("themes", "Themes"),
			("analysis", "Analysis"),
			("background", "Background"),
			("implications", "Implications"),
			("recommendations", "Recommendations"),
			("next_steps", "Next steps"),
		):
			if key not in raw:
				continue
			val = raw[key]
			if isinstance(val, list):
				bullets = _bullets_from(val)
				body = ""
				if bullets and all(isinstance(x, dict) for x in val if isinstance(val, list)):
					# dict list → body from first text fields
					chunks = []
					for item in val[:12]:
						if isinstance(item, dict):
							chunks.append(
								_as_str(
									item.get("text")
									or item.get("body")
									or item.get("summary")
									or item.get("title"),
									800,
								)
							)
					body = "\n\n".join(c for c in chunks if c)
					bullets = _bullets_from(
						[
							_as_str(item.get("title") or item.get("heading"), 200)
							for item in val
							if isinstance(item, dict)
						]
					)
				sections.append(_section(label, body, bullets))
			elif isinstance(val, dict):
				sections.append(_section(label, _as_str(val, 4000)))
			elif isinstance(val, str) and val.strip():
				sections.append(_section(label, val.strip()[:4000]))

		if not sections:
			# Dump remaining string fields as sections
			for key, val in raw.items():
				if key in {"title", "name", "topic", "subtitle", "summary", "source_note", "sources"}:
					continue
				if isinstance(val, str) and len(val.strip()) > 40:
					sections.append(_section(key.replace("_", " ").title(), val.strip()[:4000]))
				elif isinstance(val, list) and val:
					sections.append(_section(key.replace("_", " ").title(), "", _bullets_from(val)))

	return {
		"title": title,
		"subtitle": subtitle,
		"source_note": source_note if isinstance(source_note, str) else _as_str(source_note, 400),
		"sections": sections,
	}


def parse_markdown_research(text: str, *, fallback_title: str = "Research brief") -> dict[str, Any]:
	lines = (text or "").splitlines()
	title = fallback_title
	subtitle = ""
	sections: list[dict[str, Any]] = []
	cur_heading = "Overview"
	cur_body: list[str] = []
	cur_bullets: list[str] = []

	def _flush() -> None:
		nonlocal cur_heading, cur_body, cur_bullets
		body = "\n".join(cur_body).strip()
		if body or cur_bullets:
			sections.append(_section(cur_heading, body, cur_bullets))
		cur_body = []
		cur_bullets = []

	for i, line in enumerate(lines):
		heading = re.match(r"^(#{1,3})\s+(.+)$", line.strip())
		if heading:
			if i == 0 or (not sections and not cur_body and not cur_bullets and heading.group(1) == "#"):
				title = heading.group(2).strip()[:120] or title
				continue
			_flush()
			cur_heading = heading.group(2).strip()[:160] or "Section"
			continue
		bullet = re.match(r"^[-*•]\s+(.+)$", line.strip())
		if bullet:
			cur_bullets.append(bullet.group(1).strip()[:400])
			continue
		if line.strip():
			if not subtitle and not sections and not cur_body and len(line.strip()) < 180:
				subtitle = line.strip()[:240]
			else:
				cur_body.append(line.rstrip())
	_flush()
	if not sections and text.strip():
		sections = [_section("Overview", text.strip()[:4000])]
	return {
		"title": title,
		"subtitle": subtitle,
		"source_note": "Parsed from markdown research",
		"sections": sections,
	}


def load_research_file(path: Path) -> dict[str, Any] | None:
	if not path.is_file():
		return None
	try:
		raw_text = path.read_text(encoding="utf-8", errors="replace")
	except OSError:
		return None
	fallback = path.stem.replace("_", " ").replace("-", " ").title()
	if path.suffix.lower() == ".json":
		try:
			payload = json.loads(raw_text)
		except json.JSONDecodeError:
			return parse_markdown_research(raw_text, fallback_title=fallback)
		return parse_research_payload(payload, fallback_title=fallback)
	return parse_markdown_research(raw_text, fallback_title=fallback)


def research_has_sections(bundle: dict[str, Any] | None) -> bool:
	if not isinstance(bundle, dict):
		return False
	sections = bundle.get("sections")
	return isinstance(sections, list) and len(sections) > 0


def write_research_bundle(
	project_id: str,
	*,
	force: bool = False,
	message: str = "",
) -> dict[str, Any] | None:
	"""Find research files, write app/public/research.json, mirror into data-room.

	Returns the bundle dict when written (or existing valid bundle if nothing new),
	else None.
	"""
	root = project_dir(project_id)
	app_public = root / "app" / "public"
	out_path = app_public / "research.json"
	candidates = find_research_candidates(root)

	# Prefer explicit research-named files over incidental md/json
	named = [p for p in candidates if _RESEARCH_NAME.search(p.name)]
	primary = named[0] if named else None
	if primary is None and message_suggests_research(message):
		# Prefer agent work/ outputs — never invent from vendor data-room packs
		work_files = [p for p in candidates if "work" in p.parts]
		primary = work_files[0] if work_files else None

	should = force or message_suggests_research(message) or primary is not None
	if not should:
		return None

	bundle: dict[str, Any] | None = None
	if primary is not None:
		bundle = load_research_file(primary)
	elif out_path.is_file():
		try:
			existing = json.loads(out_path.read_text())
			if research_has_sections(existing):
				return existing
		except (json.JSONDecodeError, OSError):
			pass
		return None

	if not research_has_sections(bundle):
		return None

	assert bundle is not None
	if primary is not None:
		note = bundle.get("source_note") or ""
		src_note = f"From {primary.name}"
		bundle["source_note"] = f"{note} · {src_note}".strip(" ·") if note else src_note

	app_public.mkdir(parents=True, exist_ok=True)
	out_path.write_text(json.dumps(bundle, indent=2), encoding="utf-8")

	# Inventory: copy primary research into data-room if missing
	if primary is not None:
		room = root / "inputs" / "data-room"
		room.mkdir(parents=True, exist_ok=True)
		dest = room / primary.name
		try:
			primary_resolved = primary.resolve()
			dest_resolved = dest.resolve()
		except OSError:
			primary_resolved = primary
			dest_resolved = dest
		if primary_resolved != dest_resolved and not dest.exists():
			try:
				shutil.copy2(primary, dest)
			except OSError:
				pass

	return bundle


def ensure_research_aware_report_app(app_dir: Path) -> bool:
	"""If research.json has sections and App.tsx cannot fetch it, sync template App.tsx.

	Returns True when App.tsx was rewritten from the report template.
	"""
	research_path = app_dir / "public" / "research.json"
	tsx_path = app_dir / "src" / "App.tsx"
	if not research_path.is_file() or not tsx_path.is_file():
		return False
	try:
		bundle = json.loads(research_path.read_text())
	except (json.JSONDecodeError, OSError):
		return False
	if not research_has_sections(bundle):
		return False

	tsx = tsx_path.read_text(encoding="utf-8", errors="replace")
	if "research.json" in tsx:
		return False

	from .formats import template_path

	template_app = template_path("report") / "src" / "App.tsx"
	if not template_app.is_file():
		return False
	template_src = template_app.read_text(encoding="utf-8")
	if "research.json" not in template_src:
		return False
	tsx_path.write_text(template_src, encoding="utf-8")
	return True


def snapshot_research_mtimes(project_id: str) -> dict[str, float]:
	"""Capture work/research baselines before an agent turn (observe wrapper)."""
	from .observe import snapshot_work_mtimes

	return snapshot_work_mtimes(project_id)


def observe_and_promote_research(
	project_id: str,
	*,
	before: dict[str, float] | None = None,
	force: bool = False,
	artifact_kind: str | None = None,
) -> dict[str, Any]:
	"""Simulacra observer: agent wrote research → promote into the data room.

	Thin wrapper over observe.promote_work_artifacts for back-compat.
	Returns {promoted, quarantined, bundle, refreshed, section_count}.
	"""
	from .observe import promote_work_artifacts

	return promote_work_artifacts(
		project_id,
		before=before,
		force=force,
		artifact_kind=artifact_kind,
	)
