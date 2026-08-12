"""Data sources — ingestion, inventory, profiling, agent context pack.

Owns the data-room lifecycle so plan/build/iterate always design around real sources.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import FIXTURES
from .runs import project_dir

# ── Limits (Must Handle) ─────────────────────────────────────────────
MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MiB per file
MAX_FILES = 40
MAX_TOTAL_BYTES = 48 * 1024 * 1024
MAX_FILENAME_LEN = 180

EXTRACTABLE_EXT = {".md", ".txt", ".csv", ".json"}
# Accepted into the room but not extracted yet — honest skip status
KNOWN_UNSUPPORTED = {
	".pdf",
	".xlsx",
	".xls",
	".docx",
	".doc",
	".png",
	".jpg",
	".jpeg",
	".webp",
	".zip",
}

# Agent/runtime artifacts — never surface as user "sources"
_INTERNAL_ROOM_NAMES = {
	"design_brief.json",
	"plan_preview.json",
	"kernel-state.json",
	"kernel_state.json",
	"agent_context.json",
	"extract_report.json",
}


class SourceError(ValueError):
	"""User-facing sources error."""


@dataclass
class SourceFile:
	name: str
	size: int
	type: str
	sha256: str = ""
	status: str = "ready"  # ready | extractable | skipped | error
	detail: str = ""
	row_count: int = 0

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass
class ExtractFileReport:
	name: str
	status: str  # ok | empty | skipped | error
	rows: int = 0
	detail: str = ""

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


@dataclass
class ExtractReport:
	rows: list[dict[str, Any]] = field(default_factory=list)
	files: list[ExtractFileReport] = field(default_factory=list)
	errors: list[str] = field(default_factory=list)
	skipped: list[str] = field(default_factory=list)

	@property
	def ok_count(self) -> int:
		return sum(1 for f in self.files if f.status == "ok")

	def to_dict(self) -> dict[str, Any]:
		return {
			"row_count": len(self.rows),
			"files": [f.to_dict() for f in self.files],
			"errors": self.errors,
			"skipped": self.skipped,
			"ok_files": self.ok_count,
		}


@dataclass
class DataProfile:
	row_count: int = 0
	columns: list[str] = field(default_factory=list)
	vendors: list[str] = field(default_factory=list)
	themes: list[str] = field(default_factory=list)
	high_risk: int = 0
	medium_risk: int = 0
	low_risk: int = 0
	regions: list[str] = field(default_factory=list)
	owners: list[str] = field(default_factory=list)
	source_files: list[str] = field(default_factory=list)
	score_min: float | None = None
	score_max: float | None = None
	score_avg: float | None = None
	empty_room: bool = True
	suggested_primary: str = "overview"
	suggested_must_have: list[str] = field(default_factory=list)
	nuance_notes: list[str] = field(default_factory=list)

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)


def data_room_dir(project_id: str) -> Path:
	path = project_dir(project_id) / "inputs" / "data-room"
	path.mkdir(parents=True, exist_ok=True)
	return path


def _sha256_bytes(data: bytes) -> str:
	return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
	h = hashlib.sha256()
	with path.open("rb") as f:
		for chunk in iter(lambda: f.read(1024 * 64), b""):
			h.update(chunk)
	return h.hexdigest()


def safe_source_name(filename: str | None) -> str:
	"""Strip path components and dangerous chars. Raises SourceError if unusable."""
	raw = (filename or "").strip()
	if not raw:
		raise SourceError("File has no name")
	name = Path(raw.replace("\\", "/")).name
	if not name or name in (".", ".."):
		raise SourceError("Invalid file name")
	if len(name) > MAX_FILENAME_LEN:
		stem = Path(name).stem[: MAX_FILENAME_LEN - 12]
		name = f"{stem}{Path(name).suffix[:8]}"
	name = re.sub(r"[^\w.\- ()\[\]]+", "_", name, flags=re.UNICODE).strip(" ._")
	if not name or name.startswith("."):
		raise SourceError(f"Unsafe file name: {filename!r}")
	return name


def _ext(name: str) -> str:
	return Path(name).suffix.lower()


def _file_status(name: str, size: int) -> tuple[str, str]:
	from .firecrawl import FIRECRAWL_EXT, firecrawl_enabled

	ext = _ext(name)
	if size <= 0:
		return "error", "Empty file"
	if size > MAX_FILE_BYTES:
		return "error", f"Exceeds {MAX_FILE_BYTES // (1024 * 1024)} MiB limit"
	if ext in EXTRACTABLE_EXT:
		return "extractable", "Will be extracted into findings"
	if ext in FIRECRAWL_EXT:
		if firecrawl_enabled():
			return "extractable", "Will be parsed via Firecrawl → findings"
		return "skipped", f"{ext} needs FIRECRAWL_API_KEY"
	if ext in KNOWN_UNSUPPORTED:
		return "skipped", f"{ext} not extractable yet — kept for inventory"
	return "skipped", f"Unknown type {ext or '(none)'} — kept for inventory"


def list_source_files(project_id: str) -> list[SourceFile]:
	root = data_room_dir(project_id)
	out: list[SourceFile] = []
	for path in sorted(root.rglob("*")):
		if not path.is_file():
			continue
		if path.name.lower() in _INTERNAL_ROOM_NAMES:
			continue
		rel = str(path.relative_to(root))
		size = path.stat().st_size
		status, detail = _file_status(rel, size)
		try:
			digest = _sha256_file(path)
		except OSError:
			digest = ""
			status, detail = "error", "Unreadable"
		out.append(
			SourceFile(
				name=rel,
				size=size,
				type=path.suffix.lstrip(".").lower(),
				sha256=digest,
				status=status,
				detail=detail,
			)
		)
	return out


def content_fingerprint(project_id: str) -> str:
	"""Stable hash of room contents — detect stale extracts."""
	h = hashlib.sha256()
	for src in list_source_files(project_id):
		h.update(src.name.encode())
		h.update(src.sha256.encode())
		h.update(str(src.size).encode())
	return h.hexdigest()


def _assert_room_capacity(project_id: str, *, extra_files: int = 0, extra_bytes: int = 0) -> None:
	files = list_source_files(project_id)
	if len(files) + extra_files > MAX_FILES:
		raise SourceError(f"Data room capped at {MAX_FILES} files")
	total = sum(f.size for f in files) + extra_bytes
	if total > MAX_TOTAL_BYTES:
		raise SourceError(f"Data room capped at {MAX_TOTAL_BYTES // (1024 * 1024)} MiB total")


def seed_fixtures(project_id: str, *, clear: bool = False) -> list[SourceFile]:
	"""Copy golden fixture pack into the project data room."""
	root = data_room_dir(project_id)
	if clear and root.exists():
		shutil.rmtree(root)
		root.mkdir(parents=True, exist_ok=True)
	if not FIXTURES.exists():
		raise SourceError("Fixture data room missing on server")
	for path in sorted(FIXTURES.rglob("*")):
		if not path.is_file():
			continue
		rel = path.relative_to(FIXTURES)
		dest = root / rel
		dest.parent.mkdir(parents=True, exist_ok=True)
		shutil.copy2(path, dest)
	return list_source_files(project_id)


def clear_data_room(project_id: str) -> None:
	root = data_room_dir(project_id)
	if root.exists():
		shutil.rmtree(root)
	root.mkdir(parents=True, exist_ok=True)


def add_upload(
	project_id: str,
	*,
	filename: str | None,
	data: bytes,
	overwrite: bool = True,
) -> SourceFile:
	"""Write one upload into the data room with validation."""
	name = safe_source_name(filename)
	if len(data) > MAX_FILE_BYTES:
		raise SourceError(f"{name} exceeds {MAX_FILE_BYTES // (1024 * 1024)} MiB limit")
	if len(data) == 0:
		raise SourceError(f"{name} is empty")

	root = data_room_dir(project_id)
	dest = root / name
	existing = dest.is_file()
	extra_files = 0 if (existing and overwrite) else 1
	extra_bytes = len(data) - (dest.stat().st_size if existing else 0)
	_assert_room_capacity(project_id, extra_files=extra_files, extra_bytes=max(0, extra_bytes))

	if dest.exists() and not overwrite:
		raise SourceError(f"{name} already exists — remove it or enable overwrite")

	dest.parent.mkdir(parents=True, exist_ok=True)
	tmp = dest.with_suffix(dest.suffix + ".tmp")
	tmp.write_bytes(data)
	tmp.replace(dest)

	status, detail = _file_status(name, len(data))
	return SourceFile(
		name=name,
		size=len(data),
		type=Path(name).suffix.lstrip(".").lower(),
		sha256=_sha256_bytes(data),
		status=status,
		detail=detail,
	)


def remove_source(project_id: str, name: str) -> None:
	root = data_room_dir(project_id)
	candidate = name.replace("\\", "/").lstrip("/")
	if ".." in Path(candidate).parts:
		raise SourceError("Path traversal blocked")
	path = root / candidate
	if not path.is_file():
		path = root / safe_source_name(name)
	if not path.is_file():
		raise SourceError(f"Source not found: {name}")
	try:
		path.resolve().relative_to(root.resolve())
	except ValueError as exc:
		raise SourceError("Path escape blocked") from exc
	path.unlink()
	parent = path.parent
	while parent != root and parent.is_dir() and not any(parent.iterdir()):
		parent.rmdir()
		parent = parent.parent


def profile_rows(rows: list[dict[str, Any]]) -> DataProfile:
	"""Derive schema/stats/nuances so the agent designs around the data."""
	if not rows:
		return DataProfile(
			empty_room=True,
			nuance_notes=[
				"Data room is empty or no extractable findings — scaffold will be hollow until sources are added.",
			],
			suggested_must_have=["Empty state", "Upload CTA copy"],
		)

	cols = sorted({k for r in rows for k in r.keys()})
	vendors = sorted({str(r.get("vendor") or "") for r in rows if r.get("vendor")})
	themes = sorted({str(r.get("theme") or "") for r in rows if r.get("theme")})
	regions = sorted({str(r.get("region") or "") for r in rows if r.get("region")})
	owners = sorted({str(r.get("owner") or "") for r in rows if r.get("owner")})
	sources = sorted({str(r.get("source_file") or "") for r in rows if r.get("source_file")})
	high = sum(1 for r in rows if str(r.get("risk_level")) == "high")
	med = sum(1 for r in rows if str(r.get("risk_level")) == "medium")
	low = sum(1 for r in rows if str(r.get("risk_level")) == "low")
	scores = [float(r["risk_score"]) for r in rows if isinstance(r.get("risk_score"), (int, float))]

	notes: list[str] = []
	must = ["KPI strip", "primary table"]
	primary = "overview"

	if high / max(len(rows), 1) >= 0.25:
		notes.append(f"High-risk density is elevated ({high}/{len(rows)}) — lead with severity triage.")
		must.insert(0, "High-risk triage")
		primary = "findings"
	# Only suggest a vendor leaderboard when the DATA actually has multiple vendors —
	# never because the product default is "Vendor Risk".
	if len(vendors) >= 5:
		notes.append(f"{len(vendors)} entities in vendor field — leaderboard/scorecard is useful.")
		must.append("entity leaderboard")
	elif len(vendors) == 1:
		notes.append(
			f"Single entity ({vendors[0]}) — deepen theme/evidence, de-emphasize multi-entity chrome."
		)
		must.append("theme breakdown")
	if regions:
		notes.append(f"Region field populated ({len(regions)} values) — region filter/heatmap useful.")
		must.append("region filter")
	if owners:
		notes.append(f"Owner field populated ({len(owners)} values) — ownership column matters.")
	if not scores:
		notes.append("No numeric scores — avoid score histogram; emphasize counts and levels.")
	elif max(scores) - min(scores) < 5:
		notes.append("Scores are tightly clustered — histogram may look flat; prefer ranked lists.")
	if len(rows) < 8:
		notes.append("Small row set — prefer dense tables over sparse multi-panel dashboards.")
	elif len(rows) > 200:
		notes.append("Large row set — search + filters required; paginate findings.")
		must.append("search and filters")

	if len(sources) == 1:
		notes.append(f"All findings from one source ({sources[0]}) — cite it in the header.")
	elif len(sources) > 1:
		must.append("source inventory")

	return DataProfile(
		row_count=len(rows),
		columns=cols,
		vendors=vendors[:40],
		themes=themes[:40],
		high_risk=high,
		medium_risk=med,
		low_risk=low,
		regions=regions[:30],
		owners=owners[:30],
		source_files=sources[:40],
		score_min=min(scores) if scores else None,
		score_max=max(scores) if scores else None,
		score_avg=round(sum(scores) / len(scores), 1) if scores else None,
		empty_room=False,
		suggested_primary=primary,
		suggested_must_have=must,
		nuance_notes=notes,
	)


def apply_profile_to_brief(brief: dict[str, Any], profile: DataProfile) -> dict[str, Any]:
	"""Merge data-driven IA hints into the design brief (non-destructive)."""
	out = copy.deepcopy(brief)
	ia = out.setdefault("information_architecture", {})
	if profile.suggested_primary and not ia.get("primary_view"):
		ia["primary_view"] = profile.suggested_primary
	existing = list(ia.get("must_have") or [])
	for item in profile.suggested_must_have:
		if item not in existing:
			existing.append(item)
	ia["must_have"] = existing[:12]
	notes = (out.get("user_notes") or "").strip()
	data_note = "Data profile: " + "; ".join(profile.nuance_notes[:4])
	if profile.nuance_notes and data_note not in notes:
		out["user_notes"] = f"{notes}\n{data_note}".strip() if notes else data_note
	return out


def _excerpt(path: Path, *, limit: int = 1800) -> str:
	try:
		text = path.read_text(encoding="utf-8", errors="replace")
	except OSError:
		return "(unreadable)"
	if len(text) <= limit:
		return text
	return text[:limit] + "\n… [truncated]"


def write_agent_context(
	project_id: str,
	*,
	rows: list[dict[str, Any]],
	profile: DataProfile,
	extract: ExtractReport | None = None,
	prompt: str = "",
) -> dict[str, Path]:
	"""Write agent-facing context packs under work/ and app/public/."""
	root = project_dir(project_id)
	work = root / "work"
	pub = root / "app" / "public"
	work.mkdir(parents=True, exist_ok=True)
	pub.mkdir(parents=True, exist_ok=True)

	sources = list_source_files(project_id)
	by_name = {f.name: f for f in (extract.files if extract else [])}
	for s in sources:
		rep = by_name.get(s.name)
		if rep:
			s.row_count = rep.rows
			if rep.status == "error":
				s.status = "error"
				s.detail = rep.detail
			elif rep.status == "skipped":
				s.status = "skipped"
				s.detail = rep.detail or s.detail

	inventory = {
		"generated_at": datetime.now(UTC).isoformat(),
		"fingerprint": content_fingerprint(project_id),
		"prompt": prompt[:500],
		"sources": [s.to_dict() for s in sources],
		"extract": extract.to_dict() if extract else None,
		"profile": profile.to_dict(),
		"sample_rows": rows[:8],
	}

	sources_json = pub / "sources.json"
	profile_json = pub / "data_profile.json"
	context_md = work / "agent_context.md"
	app_context = pub / "agent_context.md"

	sources_json.write_text(json.dumps(inventory, indent=2, default=str))
	profile_json.write_text(json.dumps(profile.to_dict(), indent=2))

	lines = [
		"# Agent context — data room",
		"",
		f"**Task:** {prompt[:300] or '(none)'}",
		f"**Findings:** {profile.row_count} · high={profile.high_risk} med={profile.medium_risk} low={profile.low_risk}",
		f"**Vendors:** {', '.join(profile.vendors[:12]) or 'none'}",
		f"**Columns:** {', '.join(profile.columns) or 'none'}",
		f"**Fingerprint:** `{inventory['fingerprint'][:16]}…`",
		"",
		"## Design around this data",
	]
	for note in profile.nuance_notes:
		lines.append(f"- {note}")
	if not profile.nuance_notes:
		lines.append("- (no special nuances)")
	lines.extend(["", "## Source inventory"])
	for s in sources:
		lines.append(f"- `{s.name}` ({s.size} B, {s.status}) — {s.detail} · rows={s.row_count}")
	if extract and extract.errors:
		lines.extend(["", "## Extract errors"])
		for err in extract.errors[:20]:
			lines.append(f"- {err}")
	lines.extend(
		[
			"",
			"## Sample rows (JSON)",
			"```json",
			json.dumps(rows[:5], indent=2, default=str)[:2500],
			"```",
		]
	)

	room = data_room_dir(project_id)
	excerpts = 0
	lines.extend(["", "## Source excerpts"])
	for s in sources:
		if excerpts >= 3:
			break
		path = room / s.name
		if not path.is_file() or _ext(s.name) not in EXTRACTABLE_EXT:
			continue
		lines.append(f"### `{s.name}`")
		lines.append("```")
		lines.append(_excerpt(path))
		lines.append("```")
		excerpts += 1
	if excerpts == 0:
		lines.append("(no extractable text excerpts)")

	body = "\n".join(lines) + "\n"
	context_md.write_text(body)
	app_context.write_text(body)

	if extract:
		(work / "extract_report.json").write_text(json.dumps(extract.to_dict(), indent=2))

	return {
		"sources_json": sources_json,
		"profile_json": profile_json,
		"agent_context": context_md,
		"app_agent_context": app_context,
	}


def sources_to_prime_block(profile: DataProfile, *, extract: ExtractReport | None = None) -> str:
	"""Compact block injected into plan/builder agent prompts."""
	lines = [
		"## Data room (design the app around THIS)",
		f"- Rows: {profile.row_count} (high={profile.high_risk}, medium={profile.medium_risk}, low={profile.low_risk})",
		f"- Vendors ({len(profile.vendors)}): {', '.join(profile.vendors[:15]) or 'none'}",
		f"- Themes ({len(profile.themes)}): {', '.join(profile.themes[:12]) or 'none'}",
		f"- Columns: {', '.join(profile.columns) or 'none'}",
		f"- Source files: {', '.join(profile.source_files[:10]) or 'none'}",
	]
	if profile.score_avg is not None:
		lines.append(
			f"- Scores: min={profile.score_min} avg={profile.score_avg} max={profile.score_max}"
		)
	if profile.regions:
		lines.append(f"- Regions: {', '.join(profile.regions[:10])}")
	if profile.owners:
		lines.append(f"- Owners: {', '.join(profile.owners[:10])}")
	lines.append(f"- Suggested primary view: {profile.suggested_primary}")
	lines.append(f"- Suggested must_have: {', '.join(profile.suggested_must_have)}")
	lines.append("### Nuances")
	for note in profile.nuance_notes[:8]:
		lines.append(f"- {note}")
	if extract:
		lines.append(
			f"### Extract: {extract.ok_count} ok files, "
			f"{len(extract.skipped)} skipped, {len(extract.errors)} errors"
		)
		for err in extract.errors[:5]:
			lines.append(f"- error: {err}")
	lines.append(
		"Read `public/sources.json`, `public/data_profile.json`, and `public/agent_context.md` "
		"before editing layout. Prefer viz that match the nuances above."
	)
	if profile.empty_room:
		lines.append(
			"CRITICAL: empty room — do not invent vendors/findings; show an honest empty state."
		)
	return "\n".join(lines)


# ── Soft inventory for plan UI / Prime (not a build gate) ─────────────

_FIXTURE_NAMES = frozenset({"notes.json", "supplement.csv", "vendor-research.md"})


def source_room_brief(preview: dict[str, Any] | None) -> dict[str, Any]:
	"""Honest inventory of what's in the data room — for chat UI and Prime context.

	Does **not** decide whether to build. User + Prime steer: upload, sample pack,
	or research/scrape if the user asks.
	"""
	preview = preview or {}
	files = [f for f in (preview.get("files") or []) if isinstance(f, dict)]
	names = [(f.get("name") or "").strip() for f in files if f.get("name")]
	rows = int(preview.get("row_count") or 0)
	name_set = {n.lower() for n in names}
	vendor_sample = bool(_FIXTURE_NAMES & name_set) or any(
		"vendor-research" in n for n in name_set
	)
	return {
		"empty": rows <= 0 and not names,
		"row_count": rows,
		"file_count": len(names),
		"file_names": names[:12],
		"vendors": list(preview.get("vendors") or [])[:12],
		"looks_like_vendor_sample": vendor_sample,
	}


def source_room_lines(brief: dict[str, Any]) -> list[str]:
	"""Short human lines for plan chrome / agent prompts."""
	if brief.get("empty"):
		return ["No sources attached yet"]
	names = ", ".join(brief.get("file_names") or []) or "files"
	rows = int(brief.get("row_count") or 0)
	line = f"{brief.get('file_count', 0)} files"
	if rows:
		line += f" · {rows} rows"
	line += f" ({names})"
	out = [line]
	if brief.get("looks_like_vendor_sample"):
		out.append("Attached pack looks like the vendor-risk sample")
	return out
