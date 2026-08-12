"""Observe → intervene: small product hooks after agent turns and before builds.

Priority on doubt: the agent wins. Soft and additive only — promote files, heal
status, prewarm Build. Never rewrite agent replies or hard-block Build/iterate
because Simulacra is unsure.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from .runs import ProjectState, load_state, project_dir, save_state
from .sources import (
	MAX_FILE_BYTES,
	SourceError,
	content_fingerprint,
	data_room_dir,
	list_source_files,
	safe_source_name,
	source_room_brief,
)

PROMOTE_EXT = frozenset({".json", ".md", ".txt", ".csv", ".pdf", ".tsv"})
_RESEARCH_NAME = re.compile(r"research|bjp", re.I)
_SECRET_NAME = re.compile(
	r"(^\.env(\.|$))|credential|secret|(^|[/\\])id_rsa(\.|$)",
	re.I,
)
_STYLE_HINT = re.compile(
	r"\b(dense|soft|editorial|playful|accent|dark|light|font|minimal|"
	r"spacious|compact|utilitarian|chrome|palette|theme)\b",
	re.I,
)
_SKIP_DIR = frozenset(
	{"node_modules", ".git", "dist", ".venv", "__pycache__", ".next", "quarantine"}
)
_VENDOR_PROMPT = re.compile(
	r"\b(vendor|third[- ]?party|tprm|supplier|diligence|risk\s*register)\b",
	re.I,
)


def _skip_dir(path: Path, root: Path) -> bool:
	try:
		parts = path.relative_to(root).parts
	except ValueError:
		parts = path.parts
	return any(p in _SKIP_DIR for p in parts)


def _work_candidates(root: Path) -> list[Path]:
	"""Promotable files under work/ plus research-named files at project root."""
	found: list[Path] = []
	seen: set[Path] = set()

	def _add(path: Path) -> None:
		if not path.is_file():
			return
		if _skip_dir(path, root):
			return
		ext = path.suffix.lower()
		if ext not in PROMOTE_EXT:
			return
		try:
			rel = str(path.relative_to(root)).replace("\\", "/")
		except ValueError:
			rel = path.name
		# Agent scratch stub — not a source
		if rel in ("work/research/README.md", "work/research/readme.md"):
			return
		if rel.startswith("work/quarantine/"):
			return
		# Runtime / agent internals — never promote into the user data room
		base = path.name.lower()
		if base in {
			"design_brief.json",
			"plan_preview.json",
			"kernel-state.json",
			"kernel_state.json",
			"agent_context.json",
			"extract_report.json",
		}:
			return
		try:
			resolved = path.resolve()
		except OSError:
			return
		if resolved in seen:
			return
		seen.add(resolved)
		found.append(path)

	work = root / "work"
	if work.is_dir():
		for path in work.rglob("*"):
			if path.is_file():
				_add(path)

	# Research-named at project root (not under app/inputs)
	if root.is_dir():
		for path in root.iterdir():
			if path.is_file() and _RESEARCH_NAME.search(path.name):
				_add(path)

	return found


def snapshot_work_mtimes(project_id: str) -> dict[str, float]:
	"""Capture work/ (+ research-named root) mtimes before an agent turn."""
	root = project_dir(project_id)
	out: dict[str, float] = {}
	for path in _work_candidates(root):
		try:
			out[str(path.resolve())] = path.stat().st_mtime
		except OSError:
			continue
	return out


def assert_promotable(path: Path) -> None:
	"""Raise SourceError when a file must not enter the data room."""
	reason = _reject_reason(path)
	if reason:
		raise SourceError(reason)


def _reject_reason(path: Path) -> str | None:
	name = path.name
	lower = name.lower()
	if lower == ".env" or lower.startswith(".env."):
		return "Reject .env — secrets stay out of the data room"
	if _SECRET_NAME.search(name) or "credential" in lower or "secret" in lower:
		return f"Reject secret-like name: {name}"
	if lower == "id_rsa" or lower.startswith("id_rsa."):
		return "Reject private key material"
	try:
		size = path.stat().st_size
	except OSError:
		return f"Unreadable: {name}"
	if size > MAX_FILE_BYTES:
		return f"Exceeds {MAX_FILE_BYTES // (1024 * 1024)} MiB limit"
	if size <= 0:
		return "Empty file"
	ext = path.suffix.lower()
	if ext not in PROMOTE_EXT:
		return f"Unsupported type {ext or '(none)'}"
	return None


def _quarantine_copy(project_id: str, path: Path, reason: str) -> str:
	root = project_dir(project_id)
	qdir = root / "work" / "quarantine"
	qdir.mkdir(parents=True, exist_ok=True)
	dest = qdir / path.name
	n = 1
	while dest.exists():
		dest = qdir / f"{path.stem}_{n}{path.suffix}"
		n += 1
	try:
		shutil.copy2(path, dest)
		(dest.with_suffix(dest.suffix + ".reason.txt")).write_text(reason)
	except OSError:
		pass
	return dest.name


def _secret_candidates(root: Path) -> list[Path]:
	"""Secret-like files under work/ that must be quarantined, not promoted."""
	found: list[Path] = []
	work = root / "work"
	if not work.is_dir():
		return found
	for path in work.rglob("*"):
		if not path.is_file() or _skip_dir(path, root):
			continue
		rel = str(path.relative_to(root)).replace("\\", "/")
		if rel.startswith("work/quarantine/"):
			continue
		lower = path.name.lower()
		if (
			lower == ".env"
			or lower.startswith(".env.")
			or "credential" in lower
			or "secret" in lower
			or lower == "id_rsa"
			or lower.startswith("id_rsa.")
		):
			found.append(path)
			continue
		# Oversized promotable files also quarantine
		if path.suffix.lower() in PROMOTE_EXT:
			reason = _reject_reason(path)
			if reason and "Exceeds" in reason:
				found.append(path)
	return found


def promote_work_artifacts(
	project_id: str,
	*,
	before: dict[str, float] | None = None,
	force: bool = False,
	artifact_kind: str | None = None,
) -> dict[str, Any]:
	"""Promote new/changed work artifacts into the data room; quarantine secrets.

	Returns {promoted, quarantined, refreshed, bundle, section_count}.
	"""
	from .formats import normalize_kind
	from .research_bundle import write_research_bundle

	root = project_dir(project_id)
	room = data_room_dir(project_id)
	before = before or {}
	promoted: list[str] = []
	quarantined: list[str] = []
	research_hit = False

	# Always quarantine secrets under work/ (even non-promotable extensions)
	for path in _secret_candidates(root):
		reason = _reject_reason(path) or "Reject secret-like file"
		# .env has empty suffix — _reject_reason still catches it
		if path.name.lower() == ".env" or path.name.lower().startswith(".env."):
			reason = "Reject .env — secrets stay out of the data room"
		qname = _quarantine_copy(project_id, path, reason)
		quarantined.append(qname)

	candidates = _work_candidates(root)
	targets: list[Path] = []
	for path in candidates:
		try:
			resolved = str(path.resolve())
			mtime = path.stat().st_mtime
		except OSError:
			continue
		rel = (
			str(path.relative_to(root)).replace("\\", "/")
			if path.is_relative_to(root)
			else path.name
		)
		if rel.startswith("inputs/data-room/") or "work/quarantine/" in rel:
			continue
		name_hit = bool(_RESEARCH_NAME.search(path.name))
		changed = resolved not in before or mtime > (before.get(resolved) or 0) + 0.01
		in_work = rel.startswith("work/")
		in_research_dir = rel.startswith("work/research/")
		# Everything under work/research/ always promotes (timeline.json etc. lack "research" in the name).
		if force or name_hit or in_research_dir or (in_work and changed):
			targets.append(path)

	targets.sort(
		key=lambda p: (
			0 if _RESEARCH_NAME.search(p.name) else 1,
			-(p.stat().st_mtime if p.exists() else 0),
		)
	)

	seen_names: set[str] = set()
	for path in targets:
		reason = _reject_reason(path)
		if reason:
			qname = _quarantine_copy(project_id, path, reason)
			if qname not in quarantined:
				quarantined.append(qname)
			continue
		try:
			dest_name = safe_source_name(path.name)
		except SourceError:
			qname = _quarantine_copy(project_id, path, "Unsafe file name")
			quarantined.append(qname)
			continue
		if dest_name in seen_names:
			continue
		seen_names.add(dest_name)
		dest = room / dest_name
		if dest.exists():
			try:
				if path.stat().st_mtime <= dest.stat().st_mtime + 0.01:
					continue
			except OSError:
				pass
		try:
			if path.resolve() != dest.resolve():
				shutil.copy2(path, dest)
			promoted.append(dest_name)
			if _RESEARCH_NAME.search(dest_name):
				research_hit = True
		except OSError:
			continue

	bundle = None
	kind = normalize_kind(artifact_kind) if artifact_kind else "data_app"
	if promoted or force or research_hit or any(_RESEARCH_NAME.search(p.name) for p in candidates):
		try:
			bundle = write_research_bundle(project_id, force=True, message="research")
		except Exception:  # noqa: BLE001
			bundle = None
		if bundle and kind == "report":
			app_dir = root / "app"
			if app_dir.is_dir():
				try:
					from .research_bundle import ensure_research_aware_report_app

					ensure_research_aware_report_app(app_dir)
				except Exception:  # noqa: BLE001
					pass

	refreshed = False
	if promoted:
		refreshed = _refresh_plan_inventory(project_id)

	return {
		"promoted": promoted,
		"quarantined": quarantined,
		"refreshed": refreshed,
		"bundle": bundle,
		"section_count": len((bundle or {}).get("sections") or []) if isinstance(bundle, dict) else 0,
	}


def _refresh_plan_inventory(project_id: str) -> bool:
	try:
		state = load_state(project_id)
		sources = list_source_files(project_id)
		files = [
			{
				"name": s.name,
				"size": s.size,
				"type": s.type,
				"status": s.status,
				"detail": s.detail,
				"sha256": (s.sha256 or "")[:16],
			}
			for s in sources
		]
		preview = dict(state.plan_preview or {})
		preview["files"] = files
		preview["source_room"] = source_room_brief(preview)
		state.plan_preview = preview
		save_state(state)
		return True
	except Exception:  # noqa: BLE001
		return False


def detect_topic_mismatch(state: ProjectState) -> dict[str, Any] | None:
	"""Soft signal when vendor sample is attached but prompt looks unrelated."""
	brief = source_room_brief(state.plan_preview)
	if not brief.get("looks_like_vendor_sample"):
		return None
	prompt = (state.prompt or "").strip()
	if not prompt:
		return None
	if _VENDOR_PROMPT.search(prompt):
		return None
	return {
		"ok": False,
		"reason": (
			"Attached sources look like the vendor-risk sample, "
			"but your prompt appears to be about a different topic."
		),
		"looks_like_vendor_sample": True,
		"prompt_topic": prompt[:80],
	}


def ensure_fresh_extract(project_id: str) -> bool:
	"""Re-extract when data-room fingerprint drifted from plan_preview."""
	state = load_state(project_id)
	current = content_fingerprint(project_id)
	stored = str((state.plan_preview or {}).get("fingerprint") or "")
	if stored == current:
		return False
	# First create with empty room — explore already ran; nothing to do
	if not stored and not list_source_files(project_id):
		return False
	if state.phase == "plan":
		from .plan import explore_plan_scan

		explore_plan_scan(state)
	else:
		from .pipeline import reingest_sources

		reingest_sources(project_id, refresh_preview=False)
	return True


def _dist_fp_path(project_id: str) -> Path:
	return project_dir(project_id) / "app" / "dist" / ".sources_fp"


def preview_is_stale(project_id: str) -> bool:
	"""True when dist exists but was built against an older source fingerprint."""
	root = project_dir(project_id)
	dist_index = root / "app" / "dist" / "index.html"
	if not dist_index.is_file():
		return False
	current = content_fingerprint(project_id)
	marker = _dist_fp_path(project_id)
	if marker.is_file():
		try:
			return marker.read_text().strip() != current
		except OSError:
			return True
	# No marker yet — treat as stale if plan fingerprint already drifted
	state = load_state(project_id)
	stored = str((state.plan_preview or {}).get("fingerprint") or "")
	return bool(stored and stored != current)


def ensure_preview_fresh(project_id: str) -> None:
	"""Rebuild preview when dist drifted and an app scaffold exists."""
	if not preview_is_stale(project_id):
		return
	root = project_dir(project_id)
	app_dir = root / "app"
	if not (app_dir / "package.json").is_file():
		return
	state = load_state(project_id)
	# Lazy import avoids observe ↔ pipeline ↔ deploy cycles at module load
	from .deploy import start_preview
	from .pipeline import _load_rows

	rows = _load_rows(project_id)
	if not rows:
		rows = list((state.plan_preview or {}).get("sample_rows") or [])
	try:
		url = start_preview(state, rows, app_dir=app_dir)
		state = load_state(project_id)
		state.deploy_url = url
		save_state(state)
		_dist_fp_path(project_id).write_text(content_fingerprint(project_id))
	except Exception:  # noqa: BLE001
		pass


def ensure_research_scratch(project_id: str) -> Path:
	"""Create work/research/ with a short README for the agent."""
	path = project_dir(project_id) / "work" / "research"
	path.mkdir(parents=True, exist_ok=True)
	readme = path / "README.md"
	if not readme.is_file():
		readme.write_text(
			"# Research scratch\n\n"
			"Write research notes and packs here (`*.md`, `*.json`, `*.csv`, …).\n"
			"Simulacra will promote new files into the data room after your turn.\n"
		)
	return path


def apply_style_from_message(project_id: str, message: str | None) -> bool:
	"""Merge style-ish chat into design_brief; apply CSS tokens when app exists."""
	if not message or not _STYLE_HINT.search(message):
		return False
	from .design_brief import (
		apply_brief_css_tokens,
		apply_brief_to_dist,
		merge_notes_from_message,
		resolve_palette,
		write_brief,
	)

	state = load_state(project_id)
	before = dict(state.design_brief or {})
	brief = merge_notes_from_message(state.design_brief or {}, message)
	aes = brief.setdefault("aesthetic", {})
	lower = message.lower()
	if "soft" in lower and "editorial" not in lower:
		aes["direction"] = "soft-minimal"
	if "playful" in lower:
		aes["direction"] = aes.get("direction") or "editorial"
		notes = (brief.get("user_notes") or "").strip()
		tag = "playful tone"
		if tag not in notes.lower():
			brief["user_notes"] = f"{notes}\n{tag}".strip() if notes else tag
	if "font" in lower:
		notes = (brief.get("user_notes") or "").strip()
		if message.strip() not in notes:
			brief["user_notes"] = f"{notes}\n{message.strip()}".strip() if notes else message.strip()
	aes["palette"] = resolve_palette(brief)
	state.design_brief = brief
	write_brief(project_id, brief)
	app_dir = project_dir(project_id) / "app"
	if app_dir.is_dir() and (app_dir / "package.json").is_file():
		apply_brief_css_tokens(app_dir, brief)
		if (app_dir / "dist").is_dir():
			try:
				apply_brief_to_dist(app_dir, brief)
			except Exception:  # noqa: BLE001
				pass
	save_state(state)
	return brief != before


def _normalize_prompt(prompt: str) -> str:
	return re.sub(r"\s+", " ", (prompt or "").strip().lower())


def duplicate_project_warnings(
	tenant_id: str,
	prompt: str,
	*,
	exclude_id: str | None = None,
) -> list[str]:
	"""Soft strings when a similar prompt already exists for this tenant."""
	from .runs import list_projects

	needle = _normalize_prompt(prompt)[:80]
	if not needle:
		return []
	out: list[str] = []
	for other in list_projects(tenant_id=tenant_id):
		if exclude_id and other.id == exclude_id:
			continue
		other_n = _normalize_prompt(other.prompt)[:80]
		if other_n and other_n == needle:
			title = (other.app_config.title if other.app_config else "") or other.id[:8]
			out.append(f"Similar project already exists ({title}). Continuing anyway.")
			break
	return out


def heal_display_title(state: ProjectState) -> ProjectState:
	"""Unstick stock vendor titles and raw imperative prompts as product names."""
	from .chat import infer_app_config
	from .design_brief import title_from_prompt

	title = (state.app_config.title or "").strip()
	stock = {"Vendor Risk Command Center", "Vendor Risk Dashboard"}
	prompt = (state.prompt or "").strip()
	product = str((state.design_brief or {}).get("product_name") or "").strip()
	lower = f"{prompt} {product}".lower()
	dirty = False

	if title in stock:
		# Real vendor projects keep the title
		if ("vendor" in lower or "diligence" in lower) and not any(
			x in lower for x in ("bjp", "bharatiya", "bhartiya", "replace vendor", "ignore")
		):
			return state
		if product and product not in stock and len(product) > 3:
			state.app_config.title = product[:80]
		else:
			fixed = infer_app_config(prompt or product or "Report", None)
			if fixed.title and fixed.title not in stock:
				state.app_config.title = fixed.title[:80]
			elif prompt:
				state.app_config.title = title_from_prompt(prompt)[:80]
		dirty = True
	elif prompt and title.lower().startswith(("write ", "create ", "make ", "build ", "generate ")):
		state.app_config.title = title_from_prompt(prompt)[:80]
		dirty = True

	if dirty:
		if state.design_brief is not None:
			state.design_brief["product_name"] = state.app_config.title
			liner = str(state.design_brief.get("one_liner") or "")
			if liner.lower().startswith(("write ", "create ", "make ", "build ")) or liner == "Built with Simulacra":
				state.design_brief["one_liner"] = f"{state.app_config.title} — research brief"
			if state.app_config.subtitle in ("", "Built with Simulacra", "Built from your sources"):
				state.app_config.subtitle = str(state.design_brief.get("one_liner") or "")[:120]
		save_state(state)
		return load_state(state.id)
	return state


def heal_broken_preview(state: ProjectState) -> ProjectState:
	"""Clear orphan deploy_url; heal stuck building_* when no live job."""
	from .jobs import get_job

	root = project_dir(state.id)
	dist_ok = (root / "app" / "dist" / "index.html").is_file()
	dirty = False

	if state.deploy_url and not dist_ok:
		state.deploy_url = None
		dirty = True

	live = get_job(state.id)
	job = dict(state.job or {})
	stale_build = (state.status or "") in (
		"building_app",
		"publishing_preview",
		"approved",
		"extracting",
		"gating",
	)
	if stale_build and live is None and job.get("status") not in ("running", "settling"):
		if state.deployed:
			state.status = "deployed"
		elif state.phase == "ready" or state.deploy_url:
			state.status = "ready"
		elif state.phase == "plan":
			state.status = "planning"
		else:
			state.status = "draft"
		dirty = True

	if dirty:
		save_state(state)
	return load_state(state.id)


def prewarm_for_build(project_id: str) -> None:
	"""Cheap plan-phase prep when agent requests build — never claims Built."""
	from .events import emit_event

	ensure_fresh_extract(project_id)
	state = load_state(project_id)
	if state.phase != "plan":
		return

	root = project_dir(project_id)
	app_dir = root / "app"
	if not (app_dir / "package.json").is_file():
		try:
			from .deploy import sync_app
			from .pipeline import _load_rows

			rows = _load_rows(project_id)
			if not rows:
				rows = list((state.plan_preview or {}).get("sample_rows") or [])
			sync_app(project_id, state.app_config, rows, artifact_kind=state.artifact_kind)
		except Exception:  # noqa: BLE001
			pass

	emit_event(
		project_id,
		"phase",
		label="Ready to Build",
		detail="Sources synced — hit Build when you want the artifact",
		status="done",
	)
	# Keep request=build so the UI still surfaces Build — prewarm never claims Built
	state = load_state(project_id)
	state.prime = {**state.prime, "request": "build"}
	save_state(state)
