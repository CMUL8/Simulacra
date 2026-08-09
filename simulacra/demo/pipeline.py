from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .chat import infer_app_config
from .checkpoints import list_checkpoints
from .checkpoints import rollback as do_rollback
from .checkpoints import save_checkpoint, version_label
from .deploy import refresh_app_data, start_preview, stop_preview, sync_app
from .design_brief import (
	apply_brief_css_tokens,
	write_brief,
)
from .duckdb_engine import default_preview_query, rows_to_parquet
from .events import emit_event
from .extract import extract_data_room_report, write_summary
from .gates import run_gates, write_manifest
from .jobs import JobConflictError, JobRecord, request_cancel, start_job
from .plan import (
	_purge_ephemeral_status,
	approve_plan,
	explore_plan_scan,
	init_plan,
	plan_chat,
	start_agent_chat,
)

from .prime_builder import prime_build_app
from .prime_hook import prime_meta_dict
from .research_bundle import (
	ensure_research_aware_report_app,
	message_suggests_research,
	write_research_bundle,
)
from .runs import ChatMessage, ProjectState, file_hash, load_state, project_dir, save_state
from .sandbox import prepare_project_sandbox
from .sources import (
	apply_profile_to_brief,
	content_fingerprint,
	profile_rows,
	write_agent_context,
)
from .tenants import get_tenant
from .formats import normalize_kind

log = logging.getLogger("simulacra.pipeline")


def _load_rows(project_id: str) -> list[dict[str, Any]]:
	import pyarrow.parquet as pq

	path = project_dir(project_id) / "outputs" / "table.parquet"
	if not path.exists():
		return []
	return pq.read_table(path).to_pylist()


def _change_summary_lines(
	request: str,
	changed_files: list[str],
	*,
	layout: bool,
) -> list[str]:
	"""Short bullets for chat — what the user asked + which files moved."""
	lines: list[str] = []
	note = (request or "").strip().replace("\n", " ")
	if note:
		lines.append(f"Request: {note[:160]}{'…' if len(note) > 160 else ''}")
	labels = {
		"src/App.tsx": "Layout / UI (`App.tsx`)",
		"src/styles.css": "Styles (`styles.css`)",
		"public/config.json": "Title & config",
	}
	if changed_files:
		for rel in changed_files:
			lines.append(labels.get(rel, rel))
	elif layout:
		lines.append("Layout / UI updated")
	else:
		lines.append("Preview refreshed")
	return lines


def _write_policy_snapshot(project_id: str) -> None:
	root = project_dir(project_id)
	payload = {
		"direct_system_access": False,
		"write_jail": ["outputs", "app", "work", "audit"],
		"prime_bounded": True,
		"human_approve_before_build": True,
		"integration_control_layer": True,
		"bootstrap_first": True,
	}
	(root / "audit" / "policy_snapshot.json").write_text(json.dumps(payload, indent=2))


def _collect_sources(root: Path, data_room: Path) -> list[dict[str, Any]]:
	sources: list[dict[str, Any]] = []
	if data_room.exists():
		for p in data_room.rglob("*"):
			if p.is_file():
				sources.append(
					{
						"type": "folder",
						"uri": str(p.relative_to(root)),
						"content_hash": file_hash(p),
					}
				)
	return sources


def _prepare_data_and_gates(state: ProjectState) -> tuple[ProjectState, list[dict[str, Any]], list[dict[str, Any]]]:
	"""Extract → parquet → manifest → gates. Returns (state, rows, sources) or failed state with empty rows."""
	root = project_dir(state.id)
	pid = state.id
	data_room = root / "inputs" / "data-room"
	state.status = "extracting"
	state.phase = "build"
	save_state(state)

	emit_event(pid, "phase", label="Reading data room", status="running")
	report = extract_data_room_report(data_room, project_id=pid)
	rows = report.rows
	profile = profile_rows(rows)
	state.design_brief = apply_profile_to_brief(state.design_brief or {}, profile)
	write_brief(pid, state.design_brief)
	write_agent_context(
		pid, rows=rows, profile=profile, extract=report, prompt=state.prompt
	)
	detail = f"{len(rows)} findings"
	if report.errors:
		detail += f" · {len(report.errors)} file errors"
	emit_event(pid, "phase", label="Reading data room", detail=detail, status="done")

	if not rows:
		state.status = "failed"
		state.phase = "plan"
		msg = "No rows extracted from the data room — attach extractable sources (.md/.csv/.json) and re-ingest."
		if report.skipped:
			msg += f" Skipped: {', '.join(report.skipped[:5])}."
		if report.errors:
			msg += f" Errors: {'; '.join(report.errors[:3])}."
		state.chat.append(ChatMessage(role="assistant", content=msg, source="system"))
		save_state(state)
		return state, [], []

	emit_event(pid, "phase", label="Extracting structured data", status="running")
	parquet = root / "outputs" / "table.parquet"
	rows_to_parquet(rows, parquet)
	summary_text = write_summary(rows, state.prompt)
	(root / "outputs" / "summary.md").write_text(summary_text)
	emit_event(pid, "phase", label="Extracting structured data", detail=f"{len(rows)} rows → parquet", status="done")

	sources = _collect_sources(root, data_room)
	state.app_config = infer_app_config(state.prompt, state.app_config)
	if state.design_brief.get("product_name"):
		state.app_config.title = str(state.design_brief["product_name"])[:80]
	if state.design_brief.get("one_liner"):
		state.app_config.subtitle = str(state.design_brief["one_liner"])[:120]

	state.row_count = len(rows)
	write_manifest(
		state,
		rows,
		sources[:20],
		prime={
			"session_id": state.prime.get("session_id"),
			"model": state.prime.get("model") or "pending",
			"source": "pending",
		},
	)
	_write_policy_snapshot(pid)

	state.status = "gating"
	save_state(state)
	emit_event(pid, "gate", label="Running eval gates", status="running")
	audit = run_gates(state.id)
	state.gates_status = audit["status"]
	for r in audit.get("results", []):
		emit_event(
			pid,
			"gate",
			label=r["gate"],
			detail=r.get("detail", ""),
			status="done" if r.get("passed") else "fail",
		)
	emit_event(
		pid,
		"gate",
		label="Gates complete",
		detail=audit["status"],
		status="done" if audit["status"] == "pass" else "fail",
	)

	if audit["status"] != "pass":
		state.status = "failed"
		state.phase = "plan"
		state.chat.append(
			ChatMessage(
				role="assistant",
				content="Gates failed — preview blocked. Fix data issues or adjust policy, then try again.",
				source="system",
			)
		)
		save_state(state)
		return state, [], sources

	return state, rows, sources


def _scaffold_and_preview(
	state: ProjectState,
	rows: list[dict[str, Any]],
	*,
	run_prime: bool,
	leave_in_plan: bool = False,
) -> ProjectState:
	"""Sandbox → template sync → optional deepen → preview URL."""
	pid = state.id
	root = project_dir(pid)

	try:
		tenant = get_tenant(state.tenant_id)
		sandbox_mode = tenant.policy.sandbox
	except KeyError:
		sandbox_mode = None
	state.sandbox = prepare_project_sandbox(pid, tenant_sandbox=sandbox_mode)
	emit_event(
		pid,
		"phase",
		label=f"Sandbox: {state.sandbox.get('active')}",
		detail=state.sandbox.get("trust_model", ""),
		status="done",
	)
	state.status = "building_app"
	save_state(state)

	emit_event(pid, "phase", label="Preparing craft scaffold", status="running")
	write_brief(pid, state.design_brief)
	app_dir = sync_app(state.id, state.app_config, rows)
	apply_brief_css_tokens(app_dir, state.design_brief)
	write_brief(pid, state.design_brief)
	emit_event(pid, "phase", label="Preparing craft scaffold", status="done")

	source = "template"
	if run_prime:
		from .formats import get_format

		spec = get_format(getattr(state, "artifact_kind", None))
		emit_event(
			pid,
			"phase",
			label=f"Building {spec.short.lower()}",
			detail="Customizing from your brief",
			status="running",
		)
		build_meta = prime_build_app(
			app_dir, state.prompt, project_id=pid, row_count=len(rows), kind="build_run"
		)
		source = build_meta.get("source") or ("prime" if build_meta.get("ok") else "heuristic")
		if build_meta.get("used") and not build_meta.get("ok"):
			source = build_meta.get("source") or "error"
			emit_event(pid, "think", label="Keeping scaffold (build incomplete)", status="done")
		state.prime = {
			**state.prime,
			"session_id": build_meta.get("session_id") or state.prime.get("session_id"),
			"model": build_meta.get("model") or state.prime.get("model"),
			"source": source,
			"last_error": build_meta.get("error"),
			"status": "ok" if build_meta.get("ok") or not build_meta.get("used") else "error",
			"steps": build_meta.get("events") or 0,
			"style_only": bool(build_meta.get("style_only")),
			"layout_customized": bool(build_meta.get("layout_customized")),
		}
		emit_event(pid, "phase", label=f"Building {spec.short.lower()}", status="done")
	else:
		state.prime = {
			**state.prime,
			"source": "template",
			"status": "ok",
			"last_error": None,
		}

	sources = _collect_sources(root, root / "inputs" / "data-room")
	write_manifest(state, rows, sources[:20], prime=prime_meta_dict_from_state(state))

	emit_event(pid, "phase", label="Publishing preview", status="running")
	url = start_preview(state, rows, app_dir=app_dir)
	emit_event(pid, "phase", label="Publishing preview", detail=url, status="done")
	state.deploy_url = url
	if leave_in_plan:
		# Draft ready for plan review — user must Build to leave plan
		state.phase = "plan"
		state.status = "draft"
		state.plan_approved = False
	else:
		state.phase = "ready"
		state.status = "ready"
		state.plan_approved = True
	return state


def bootstrap_project(state: ProjectState) -> ProjectState:
	"""Create path: data + gates + scaffold + builder customize + preview.

	Template sync stays behind the scenes — the user lands on a Built artifact
	(phase=ready), not a Draft waiting for an extra Build click.
	"""
	from .formats import get_format

	pid = state.id
	state, rows, _sources = _prepare_data_and_gates(state)
	if not rows:
		return state

	spec = get_format(state.artifact_kind)
	state = _scaffold_and_preview(state, rows, run_prime=True, leave_in_plan=False)
	preview = state.plan_preview or {}
	files = list(preview.get("files") or [])
	vendors = list(preview.get("vendors") or [])
	high = int(preview.get("high_risk") or 0)
	file_names = ", ".join(f["name"] for f in files[:5]) if files else "your data room"
	facts = f"{len(rows)} rows"
	if high:
		facts += f" · {high} high risk"
	if vendors:
		facts += f" · {len(vendors)} vendors"

	source = state.prime.get("source") or "prime"
	changed = [str(x) for x in (state.prime.get("changed_files") or []) if x]
	if not changed and source in ("prime", "craft"):
		changed = ["src/App.tsx", "src/styles.css"]
	change_block = "\n".join(
		f"- {line}"
		for line in _change_summary_lines(state.prompt, changed, layout=source in ("prime", "craft"))
	)
	if source in ("prime", "craft"):
		body = (
			f"## {spec.label}: {state.app_config.title}\n\n"
			f"{state.app_config.subtitle}\n\n"
			f"**Sources:** {facts}\n"
			f"{file_names}\n\n"
			"**What changed**\n"
			f"{change_block}\n\n"
			"Preview is ready — open it, then chat to refine. "
			"**Ship** when you want an approved share link.\n\n"
			"Need a clean restart? Use **Rebuild from draft**."
		)
		if source == "craft":
			body = (
				f"## {spec.label}: {state.app_config.title}\n\n"
				f"{state.app_config.subtitle}\n\n"
				f"**Sources:** {facts}\n"
				f"{file_names}\n\n"
				"**What changed**\n"
				f"{change_block}\n\n"
				"Preview is ready (layout applied from your brief). "
				"Open **Preview**, chat to refine style or content, or **Ship** when ready."
			)
	else:
		body = (
			f"## {spec.label}: {state.app_config.title}\n\n"
			f"{state.app_config.subtitle}\n\n"
			f"**Sources:** {facts}\n"
			f"{file_names}\n\n"
			f"Scaffold preview is up, but the builder did not finish customizing. "
			f"Try **Rebuild from draft**, or describe changes in chat."
		)
		source = source if source in ("error", "heuristic", "timeout") else "heuristic"

	state.chat.append(ChatMessage(role="assistant", content=body, source=source))
	save_checkpoint(state, "First build")
	emit_event(pid, "done", label=f"{spec.label} ready", detail=state.deploy_url or "", status="done")
	_purge_ephemeral_status(state)
	save_state(state)
	return state


def deepen_with_prime(project_id: str, *, reset_scaffold: bool = True) -> ProjectState:
	"""Agent customize. reset_scaffold=True for Build/Rebuild; False preserves agent edits."""
	state = load_state(project_id)
	pid = project_id
	rows = _load_rows(pid)
	app_dir = project_dir(pid) / "app"

	if not rows or not app_dir.exists():
		return build_project(state, run_prime=True)

	state.status = "building_app"
	state.phase = "build"
	save_state(state)
	emit_event(pid, "phase", label="Building app", status="running")
	if reset_scaffold:
		# Fresh craft template, then agent customizes
		app_dir = sync_app(pid, state.app_config, rows)
	else:
		# Keep agent work — only refresh data/config/tokens
		app_dir = refresh_app_data(pid, state.app_config, rows)
	write_brief(pid, state.design_brief)
	apply_brief_css_tokens(app_dir, state.design_brief)

	build_meta = prime_build_app(
		app_dir, state.prompt, project_id=pid, row_count=len(rows), kind="build_run"
	)
	source = build_meta.get("source") or ("prime" if build_meta.get("ok") else "heuristic")
	if build_meta.get("used") and not build_meta.get("ok"):
		source = build_meta.get("source") or "error"

	state = load_state(pid)
	state.prime = {
		**state.prime,
		"session_id": build_meta.get("session_id") or state.prime.get("session_id"),
		"model": build_meta.get("model") or state.prime.get("model"),
		"source": source,
		"last_error": build_meta.get("error"),
		"status": "ok" if build_meta.get("ok") or not build_meta.get("used") else "error",
		"steps": build_meta.get("events") or 0,
	}
	changed = [str(x) for x in (build_meta.get("changed_files") or []) if x]
	change_block = "\n".join(
		f"- {line}"
		for line in _change_summary_lines(
			state.prompt, changed, layout=bool(build_meta.get("layout_customized"))
		)
	)
	honesty = {
		"prime": (
			"## Built\n\n"
			"The builder customized your draft.\n\n"
			"**What changed**\n"
			f"{change_block}\n\n"
			"Chat to refine layout, viz, or copy. When it looks right, open Preview → **Ship**."
		),
		"craft": (
			"## Built\n\n"
			"Preview is ready — layout applied from your brief.\n\n"
			"**What changed**\n"
			f"{change_block}\n\n"
			"Open **Preview**, chat to refine style or content, or **Ship** when ready."
		),
		"heuristic": (
			"Styles from your brief were applied, but the builder did **not** rewrite the layout. "
			"Retry **Build app**, or describe the change in chat after a successful build."
		),
		"error": "Build did not finish. Draft preview kept — retry **Build app**.",
		"template": "Draft unchanged.",
	}.get(source, "Build finished.")

	if build_meta.get("style_only") and source not in ("error", "prime"):
		source = "heuristic"
		honesty = (
			"## Partial update\n\n"
			"Styles applied from your Style chips.\n\n"
			"**What changed**\n"
			f"{change_block}\n\n"
			"The builder did not rewrite the layout — retry **Build**."
		)

	state.prime["source"] = source
	state.prime["style_only"] = bool(build_meta.get("style_only"))
	state.prime["layout_customized"] = bool(build_meta.get("layout_customized"))
	state.prime["changed_files"] = changed

	# Mark Built BEFORE the long npm preview publish so waiters never see
	# source=prime stuck on phase=build (the E2E race fault).
	state.phase = "ready"
	state.status = "publishing_preview"
	state.plan_approved = True
	state.chat.append(
		ChatMessage(
			role="assistant",
			content=honesty,
			source=source,
		)
	)
	save_state(state)

	emit_event(pid, "phase", label="Publishing preview", status="running")
	url = start_preview(state, rows, app_dir=app_dir)
	state = load_state(pid)
	state.deploy_url = url
	state.status = "ready"
	save_checkpoint(state, "First build")
	emit_event(pid, "done", label="Build complete", detail=url, status="done")
	save_state(state)
	return state


def build_project(state: ProjectState, *, run_prime: bool = True) -> ProjectState:
	"""Full extract → gates → scaffold → optional deepen → preview."""
	pid = state.id
	state, rows, _sources = _prepare_data_and_gates(state)
	if not rows:
		return state

	state = _scaffold_and_preview(state, rows, run_prime=run_prime, leave_in_plan=False)
	source = state.prime.get("source") or ("prime" if run_prime else "template")
	honesty = {
		"prime": "Build complete — open **Preview** to review.",
		"heuristic": "Built from the draft scaffold — open **Preview**.",
		"error": "Build did not finish — shipping last good draft. You can retry.",
		"template": "Draft ready — open **Preview**, then refine or build again.",
	}.get(str(source), "Build complete.")
	state.chat.append(
		ChatMessage(
			role="assistant",
			content=f"Built **{state.app_config.title}** with {len(rows)} findings. {honesty}",
			source=str(source),
		)
	)
	save_checkpoint(state, "First build")
	emit_event(pid, "done", label="Build complete", detail=state.deploy_url or "", status="done")
	save_state(state)
	return state


def prime_meta_dict_from_state(state: ProjectState) -> dict[str, Any]:
	return {
		"session_id": state.prime.get("session_id"),
		"model": state.prime.get("model") or "simulacra",
		"source": state.prime.get("source") or "heuristic",
		"status": state.prime.get("status"),
		"steps": state.prime.get("steps"),
		"duration_ms": state.prime.get("duration_ms"),
		"error": state.prime.get("last_error"),
	}


def approve_and_build(project_id: str) -> ProjectState:
	"""Synchronous deepen (tests / CLI). Prefer start_approve_build for API."""
	approve_plan(project_id)
	return deepen_with_prime(project_id)


def start_approve_build(project_id: str, *, reset_scaffold: bool = True) -> dict[str, Any]:
	"""Non-blocking Build app / Rebuild from draft → agent customize."""
	approve_plan(project_id)

	def target(_job: JobRecord) -> None:
		cur = load_state(project_id)
		if cur.deploy_url and (project_dir(project_id) / "app").exists() and _load_rows(project_id):
			deepen_with_prime(project_id, reset_scaffold=reset_scaffold)
		else:
			build_project(cur, run_prime=True)

	try:
		job = start_job(project_id, "build_run", label="Building app", target=target)
	except JobConflictError as exc:
		raise ValueError(str(exc)) from exc
	return {"job_id": job.id, "status": "running", **project_snapshot(project_id)}


def follow_up(project_id: str, message: str) -> ProjectState:
	"""Synchronous chat (tests). Product path uses start_follow_up → Prime."""
	return plan_chat(project_id, message)


def start_follow_up(project_id: str, message: str) -> dict[str, Any]:
	"""Main chat entry — always Prime; Simulacra observes structured requests."""
	start_agent_chat(project_id, message)
	snap = project_snapshot(project_id)
	job = snap.get("job") or {}
	return {"job_id": job.get("id"), "status": job.get("status") or "running", **snap}


def _iterate_merge_app_config(state: ProjectState, message: str) -> None:
	"""Avoid renaming report/topic titles back to Vendor Risk on iterate.

	Skip infer_app_config when a real title/product exists or the artifact is a
	document format. Only merge non-destructive layout toggles when we do run it
	for placeholder data apps.
	"""
	kind = normalize_kind(state.artifact_kind)
	if kind in ("report", "slides", "one_pager"):
		return
	title = (state.app_config.title or "").strip()
	placeholders = {"", "Data Explorer", "Data App", "Custom App"}
	product = str((state.design_brief or {}).get("product_name") or "").strip()
	if title not in placeholders or product:
		return
	state.app_config = infer_app_config(message, state.app_config)


def _iterate_ui(project_id: str, message: str) -> None:
	state = load_state(project_id)
	# Don't snapshot "Before:" — Versions menu only keeps meaningful restores
	emit_event(project_id, "phase", label="Builder updating app", detail=message[:120], status="running")
	rows = _load_rows(project_id)
	# Preserve prior agent work — never wipe scaffold on chat iterate
	app_dir = refresh_app_data(project_id, state.app_config, rows)
	write_brief(project_id, state.design_brief)
	apply_brief_css_tokens(app_dir, state.design_brief)

	kind = normalize_kind(state.artifact_kind)
	research_bundle = None
	# Research → public/research.json before builder (reports / non-apps)
	if kind != "data_app" or message_suggests_research(message):
		try:
			research_bundle = write_research_bundle(
				project_id,
				force=kind == "report" or message_suggests_research(message),
				message=message,
			)
			if research_bundle and kind == "report":
				ensure_research_aware_report_app(app_dir)
		except Exception:  # noqa: BLE001
			log.exception("research bundle failed for %s", project_id)

	meta = prime_build_app(
		app_dir,
		f"{state.prompt}\n\nFollow-up: {message}",
		project_id=project_id,
		row_count=len(rows),
		delta_note=message,
		kind="iterate_run",
	)
	source = meta.get("source") or ("prime" if meta.get("ok") else "heuristic")
	state = load_state(project_id)
	_iterate_merge_app_config(state, message)
	if research_bundle and kind == "report":
		rtitle = str(research_bundle.get("title") or "").strip()
		rsub = str(research_bundle.get("subtitle") or "").strip()
		if rtitle:
			state.app_config.title = rtitle[:80]
		if rsub:
			state.app_config.subtitle = rsub[:120]
		# Keep public/config.json in sync for the upcoming preview build
		try:
			refresh_app_data(project_id, state.app_config, rows)
			# refresh does not wipe research.json (only data/analytics/config)
		except Exception:  # noqa: BLE001
			pass
	state.prime = {
		**state.prime,
		"source": source,
		"last_error": meta.get("error"),
		"status": "ok" if meta.get("ok") or not meta.get("used") else "error",
		"style_only": bool(meta.get("style_only")),
	}

	changed = [str(x) for x in (meta.get("changed_files") or []) if x]
	change_lines = _change_summary_lines(message, changed, layout=bool(meta.get("layout_customized")))
	if research_bundle and kind == "report":
		change_lines = ["Research content wired into the report", *change_lines]

	# Full "## Updated" only for real prime content wins — not craft / style_only
	content_win = (
		meta.get("ok")
		and meta.get("files_changed")
		and not meta.get("style_only")
		and meta.get("source") == "prime"
	)
	research_ready = bool(research_bundle) and kind == "report"
	if content_win:
		honesty = (
			"## Updated\n\n"
			"Preview refreshed with your request.\n\n"
			"**What changed**\n"
			+ "\n".join(f"- {line}" for line in change_lines)
			+ "\n\nOpen **Preview** to check, keep chatting to refine, or **Ship** for a share link."
		)
		source = "prime"
	elif research_ready:
		honesty = (
			"## Preview refreshed\n\n"
			"Report content now comes from your research — not the vendor sample layout.\n\n"
			"**What changed**\n"
			+ "\n".join(f"- {line}" for line in change_lines)
			+ "\n\nOpen **Preview** to check, or keep chatting to refine."
		)
		source = "prime" if meta.get("source") == "prime" else "craft"
	elif (
		meta.get("source") == "craft"
		and meta.get("layout_customized")
		and not meta.get("style_only")
	):
		honesty = (
			"## Preview refreshed\n\n"
			"Report layout was updated from your brief.\n\n"
			"**What changed**\n"
			+ "\n".join(f"- {line}" for line in change_lines)
			+ "\n\nOpen **Preview** to check, or keep chatting to refine."
		)
		source = "craft"
	elif meta.get("style_only") or meta.get("source") == "craft" or (
		meta.get("files_changed") and not meta.get("ok")
	):
		honesty = (
			"## Partial update\n\n"
			"Some styles landed, but the content rewrite did **not** finish.\n\n"
			"**What changed**\n"
			+ "\n".join(f"- {line}" for line in change_lines)
			+ "\n\nRephrase the change and send again — Preview may still show the prior layout."
		)
		source = "craft" if meta.get("source") == "craft" else "heuristic"
	elif not meta.get("used"):
		honesty = "Builder is offline — could not edit the app. Try again when the agent is available."
		source = "error"
	else:
		honesty = (
			"Builder did not finish this change. Last good preview kept — try a clearer instruction."
		)
		source = meta.get("source") or "error"

	state.prime["source"] = source
	state.prime["layout_customized"] = bool(meta.get("layout_customized")) or research_ready
	state.prime["changed_files"] = changed
	state.chat.append(ChatMessage(role="assistant", content=honesty, source=source))
	state.status = "publishing_preview"
	save_state(state)
	url = start_preview(state, rows, app_dir=app_dir)
	state = load_state(project_id)
	state.deploy_url = url
	state.status = "ready"
	# Only keep a version when something useful landed
	if source in ("prime", "craft") or (changed and source != "error") or research_ready:
		save_checkpoint(state, version_label(message))
	emit_event(project_id, "done", label="Preview updated", detail=url, status="done")
	save_state(state)


def cancel_job(project_id: str) -> dict[str, Any]:
	result = request_cancel(project_id)
	state = load_state(project_id)
	if result.get("ok") and not result.get("already_idle"):
		state.chat.append(
			ChatMessage(
				role="assistant",
				content="**Stopped** — last good preview kept. You can refine or Approve again.",
				source="system",
			)
		)
		state.prime["status"] = "cancelled"
		state.prime["source"] = "cancelled"
		save_state(state)
	# Always return a full snapshot so the console can unlock
	return {**result, **project_snapshot(project_id)}


def rollback_project(project_id: str, checkpoint_id: str | None = None) -> ProjectState:
	state = do_rollback(project_id, checkpoint_id)
	rows = _load_rows(project_id)
	if rows and state.deploy_url:
		url = start_preview(state, rows)
		state.deploy_url = url
		save_state(state)
	return state


def reingest_sources(project_id: str, *, refresh_preview: bool = True) -> ProjectState:
	"""Re-extract data room → update plan preview, agent context, app public JSON.

	Call after upload/remove/seed. Safe to run in plan or ready phase.
	Does not wipe agent App.tsx edits — only refreshes data artifacts.
	"""
	state = load_state(project_id)
	pid = project_id
	emit_event(pid, "phase", label="Re-ingesting sources", status="running")
	prev_fp = (state.plan_preview or {}).get("fingerprint")
	state = explore_plan_scan(state)
	preview = state.plan_preview or {}
	rows = list(preview.get("sample_rows") or [])
	# explore only keeps 5 samples — re-extract full for parquet
	root = project_dir(pid)
	report = extract_data_room_report(root / "inputs" / "data-room", project_id=pid)
	rows = report.rows
	profile = profile_rows(rows)
	fp = content_fingerprint(pid)

	if rows:
		rows_to_parquet(rows, root / "outputs" / "table.parquet")
		(root / "outputs" / "summary.md").write_text(write_summary(rows, state.prompt))
		sources = _collect_sources(root, root / "inputs" / "data-room")
		write_manifest(
			state,
			rows,
			sources[:20],
			prime={
				"session_id": state.prime.get("session_id"),
				"model": state.prime.get("model") or "pending",
				"source": state.prime.get("source") or "pending",
			},
		)
		# Re-run gates when we have rows
		state.status = "gating"
		save_state(state)
		audit = run_gates(pid)
		state.gates_status = audit.get("status", "fail")
	else:
		state.gates_status = "pending"
		state.row_count = 0

	write_agent_context(
		pid, rows=rows, profile=profile, extract=report, prompt=state.prompt
	)
	app_dir = root / "app"
	if app_dir.exists() and (app_dir / "package.json").exists() and rows:
		refresh_app_data(pid, state.app_config, rows)
		if refresh_preview and (
			state.phase in ("ready", "build") or (app_dir / "dist" / "index.html").is_file()
		):
			try:
				url = start_preview(state, rows, app_dir=app_dir)
				state.deploy_url = url
			except Exception as exc:  # noqa: BLE001
				emit_event(
					pid,
					"think",
					label="Preview refresh skipped",
					detail=str(exc)[:200],
					status="done",
				)

	from .sources import source_room_brief

	preview = dict(state.plan_preview or {})
	preview["source_room"] = source_room_brief(preview)
	state.plan_preview = preview

	changed = prev_fp and prev_fp != fp
	n_files = len(preview.get("files") or [])
	msg = (
		f"**Sources updated.** {len(rows)} findings from {n_files} file(s)."
		+ (" Content changed — preview data refreshed." if changed else "")
	)
	if report.errors:
		msg += f"\n\nExtract issues: {'; '.join(report.errors[:3])}"
	if report.skipped:
		msg += f"\n\nSkipped (not extractable): {', '.join(report.skipped[:5])}"
	if not rows:
		msg = (
			"**Sources updated**, but nothing extractable yet. "
			"Add `.md` / `.csv` / `.json`, or tell the agent in chat how you want material gathered."
		)
	elif state.phase == "plan":
		msg += "\n\nChat with the agent about next steps, or hit **Build** when ready."
	state.chat.append(ChatMessage(role="assistant", content=msg, source="system"))
	state.status = "draft" if state.phase == "plan" else state.status
	if state.phase == "plan" and rows:
		state.status = "draft"
	elif rows and state.phase == "ready":
		state.status = "ready"
	save_state(state)
	emit_event(
		pid,
		"done",
		label="Re-ingest complete",
		detail=f"{len(rows)} rows · fp={fp[:10]}",
		status="done",
	)
	return state


def start_reingest(project_id: str) -> ProjectState:
	"""Queue re-ingest as a background job (one job at a time)."""
	state = load_state(project_id)

	def target(_job: JobRecord) -> ProjectState:
		return reingest_sources(project_id)

	try:
		start_job(project_id, "reingest", label="Re-ingesting sources", target=target)
	except JobConflictError as exc:
		raise ValueError(str(exc)) from exc
	return load_state(project_id)


def approve_deploy(project_id: str, *, public_base: str | None = None) -> ProjectState:
	import os

	state = load_state(project_id)
	if state.gates_status != "pass":
		raise ValueError("Gates must pass before deploy")
	state.deployed = True
	state.status = "deployed"
	from .deploy import preview_path

	# Always prefer same-origin preview path
	if (project_dir(project_id) / "app" / "dist" / "index.html").is_file():
		state.deploy_url = preview_path(project_id)
	elif not state.deploy_url or "127.0.0.1" in str(state.deploy_url):
		state.deploy_url = preview_path(project_id)

	path = state.deploy_url or preview_path(project_id)
	public = (
		(public_base or "").strip()
		or (os.environ.get("SIMULACRA_PUBLIC_BASE") or "").strip()
		or (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip()
	)
	if public and not public.startswith("http"):
		public = f"https://{public}"
	share = f"{public.rstrip('/')}{path}" if public else path

	state.chat.append(
		ChatMessage(
			role="assistant",
			content=(
				"## Shipped\n\n"
				"Approved for your team. Share this preview:\n\n"
				f"[Open preview]({share})\n\n"
				f"`{share}`\n\n"
				"Keep chatting to iterate — changes stay on this same link."
			),
			source="ship",
		)
	)
	save_state(state)
	return state


def project_snapshot(project_id: str) -> dict:
	from .deploy import preview_path
	from .jobs import get_job

	state = load_state(project_id)
	# Heal ghost "running" after process restart (in-memory job gone)
	live = get_job(project_id)
	job = dict(state.job or {})
	dirty = False
	if job.get("status") in ("running", "settling") and live is None:
		job["status"] = "idle"
		state.job = job
		dirty = True

	before = len(state.chat)
	_purge_ephemeral_status(state)
	if len(state.chat) != before:
		dirty = True

	# Honest subtitle — never leave a fake "Built from your sources" claim
	sub = (state.app_config.subtitle or "").strip()
	if sub == "Built from your sources":
		src = (state.prime or {}).get("source")
		if src in ("prime", "craft"):
			state.app_config.subtitle = ""
		else:
			state.app_config.subtitle = "Chat with the agent — Build when ready"
		dirty = True
	elif sub == "Data Explorer" and state.phase == "plan":
		state.app_config.subtitle = "Chat with the agent — Build when ready"
		dirty = True

	# Heal stuck in-progress status when no live job is running
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

	# Heal legacy localhost preview URLs → same-origin static path
	dist_ok = (project_dir(project_id) / "app" / "dist" / "index.html").is_file()
	url = state.deploy_url
	if dist_ok and (not url or "127.0.0.1" in str(url) or "localhost" in str(url)):
		url = preview_path(project_id)
		if state.deploy_url != url:
			state.deploy_url = url
			save_state(state)

	parquet = project_dir(project_id) / "outputs" / "table.parquet"
	if parquet.exists():
		preview = default_preview_query(project_id)
	elif state.plan_preview.get("sample_rows"):
		sample = state.plan_preview["sample_rows"]
		cols = list(sample[0].keys()) if sample else []
		preview = {
			"columns": cols,
			"rows": sample,
			"row_count": state.plan_preview.get("row_count", len(sample)),
		}
	else:
		preview = {"columns": [], "rows": [], "row_count": 0}
	return {
		"project": {
			**state.to_dict(),
			"checkpoints": list_checkpoints(project_id),
		},
		"preview_data": preview,
		"preview_url": url,
		"job": state.job,
	}


def export_audit_zip(project_id: str, dest: Path | None = None) -> Path:
	import zipfile

	root = project_dir(project_id)
	dest = dest or (root / "audit" / f"{project_id}-audit.zip")
	dest.parent.mkdir(parents=True, exist_ok=True)
	with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
		for folder in ("audit", "outputs"):
			base = root / folder
			if not base.exists():
				continue
			for path in base.rglob("*"):
				if path.is_file() and path.suffix != ".zip":
					zf.write(path, arcname=str(path.relative_to(root)))
		state_file = root / "state.json"
		if state_file.exists():
			zf.write(state_file, arcname="state.json")
	return dest
