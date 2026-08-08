from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .chat import apply_follow_up, infer_app_config
from .checkpoints import rollback as do_rollback
from .checkpoints import save_checkpoint
from .deploy import start_preview, stop_preview, sync_app
from .design_brief import (
	apply_brief_css_tokens,
	merge_notes_from_message,
	write_brief,
)
from .duckdb_engine import default_preview_query, rows_to_parquet
from .events import emit_event
from .extract import extract_data_room, write_summary
from .gates import run_gates, write_manifest
from .jobs import JobConflictError, JobRecord, request_cancel, start_job
from .plan import approve_plan, init_plan, plan_chat, start_plan_chat

from .prime_builder import prime_build_app
from .prime_hook import is_ui_change_request, prime_follow_up, prime_meta_dict
from .runs import ChatMessage, ProjectState, file_hash, load_state, project_dir, save_state
from .sandbox import prepare_project_sandbox
from .tenants import get_tenant

log = logging.getLogger("simulacra.pipeline")


def _load_rows(project_id: str) -> list[dict[str, Any]]:
	import pyarrow.parquet as pq

	path = project_dir(project_id) / "outputs" / "table.parquet"
	if not path.exists():
		return []
	return pq.read_table(path).to_pylist()


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
	rows = extract_data_room(data_room)
	emit_event(pid, "phase", label="Reading data room", detail=f"{len(rows)} findings", status="done")

	if not rows:
		state.status = "failed"
		state.phase = "plan"
		state.chat.append(
			ChatMessage(
				role="assistant",
				content="No rows extracted from the data room — attach sources and try again.",
				source="system",
			)
		)
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
) -> ProjectState:
	"""Sandbox → template sync → optional Prime → preview. Sets phase ready."""
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

	emit_event(pid, "phase", label="Syncing app template", status="running")
	write_brief(pid, state.design_brief)
	app_dir = sync_app(state.id, state.app_config, rows)
	apply_brief_css_tokens(app_dir, state.design_brief)
	write_brief(pid, state.design_brief)
	emit_event(pid, "phase", label="Syncing app template", status="done")

	source = "template"
	if run_prime:
		build_meta = prime_build_app(
			app_dir, state.prompt, project_id=pid, row_count=len(rows), kind="build_run"
		)
		source = build_meta.get("source") or ("prime" if build_meta.get("ok") else "heuristic")
		if build_meta.get("used") and not build_meta.get("ok"):
			source = build_meta.get("source") or "error"
			emit_event(pid, "think", label="Using template (Prime build skipped)", status="done")
		state.prime = {
			**state.prime,
			"session_id": build_meta.get("session_id") or state.prime.get("session_id"),
			"model": build_meta.get("model") or state.prime.get("model"),
			"source": source,
			"last_error": build_meta.get("error"),
			"status": "ok" if build_meta.get("ok") or not build_meta.get("used") else "error",
			"steps": build_meta.get("events") or 0,
		}
	else:
		state.prime = {
			**state.prime,
			"source": "template",
			"status": "ok",
			"last_error": None,
		}
		emit_event(pid, "phase", label="Template preview ready", status="done")

	sources = _collect_sources(root, root / "inputs" / "data-room")
	write_manifest(state, rows, sources[:20], prime=prime_meta_dict_from_state(state))

	emit_event(pid, "phase", label="Starting preview server", status="running")
	url = start_preview(state, rows, app_dir=app_dir)
	emit_event(pid, "phase", label="Starting preview server", detail=url, status="done")
	state.deploy_url = url
	state.phase = "ready"
	state.status = "ready"
	state.plan_approved = True
	return state


def bootstrap_project(state: ProjectState) -> ProjectState:
	"""Fast path: data + gates + template + preview. No Prime. APP_MAKER_CONTRACT."""
	pid = state.id
	state, rows, _sources = _prepare_data_and_gates(state)
	if not rows:
		return state

	state = _scaffold_and_preview(state, rows, run_prime=False)
	preview = state.plan_preview or {}
	files = list(preview.get("files") or [])
	vendors = list(preview.get("vendors") or [])
	high = int(preview.get("high_risk") or 0)
	state.chat.append(
		ChatMessage(
			role="assistant",
			content=(
				f"**{state.app_config.title}** is live as a **template** preview "
				f"({len(rows)} rows"
				+ (f", {high} high risk" if high else "")
				+ (f", {len(vendors)} vendors" if vendors else "")
				+ "). "
				f"{'Sources: ' + ', '.join(f['name'] for f in files[:4]) + '. ' if files else ''}"
				"Refine style or chat — then **Improve with Prime** for taste and depth. "
				"**Stop** always unlocks if a job hangs."
			),
			source="template",
		)
	)
	save_checkpoint(state, "Bootstrap preview")
	emit_event(pid, "done", label="Preview ready", detail=state.deploy_url or "", status="done")
	save_state(state)
	return state


def deepen_with_prime(project_id: str) -> ProjectState:
	"""Prime customize on an existing preview (or full build if none)."""
	state = load_state(project_id)
	pid = project_id
	rows = _load_rows(pid)
	app_dir = project_dir(pid) / "app"

	if not rows or not app_dir.exists():
		# Fall back to full Prime build path
		return build_project(state, run_prime=True)

	state.status = "building_app"
	state.phase = "build"
	save_state(state)
	emit_event(pid, "phase", label="Prime deepening app", status="running")
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
	honesty = {
		"prime": "Deepened with **Prime** under your design brief.",
		"heuristic": "Prime unavailable — keeping the **template** preview.",
		"error": "Prime did not finish — keeping **last good** preview.",
		"template": "Template preview unchanged.",
	}.get(source, "Deepen complete.")

	emit_event(pid, "phase", label="Refreshing preview", status="running")
	url = start_preview(state, rows, app_dir=app_dir)
	state.deploy_url = url
	state.phase = "ready"
	state.status = "ready"
	state.chat.append(
		ChatMessage(
			role="assistant",
			content=f"{honesty} Open **Preview** or keep refining.",
			source=source,
		)
	)
	save_checkpoint(state, "Prime deepen")
	emit_event(pid, "done", label="Deepen complete", detail=url, status="done")
	save_state(state)
	return state


def build_project(state: ProjectState, *, run_prime: bool = True) -> ProjectState:
	"""Full extract → gates → scaffold → optional Prime → preview."""
	pid = state.id
	state, rows, _sources = _prepare_data_and_gates(state)
	if not rows:
		return state

	state = _scaffold_and_preview(state, rows, run_prime=run_prime)
	source = state.prime.get("source") or ("prime" if run_prime else "template")
	honesty = {
		"prime": "Built with **Prime** under your design brief.",
		"heuristic": "Built with the **template + heuristics** (Prime off or unavailable).",
		"error": "Prime did not finish — shipping **last good template**. You can retry or refine.",
		"template": "Built as a **template** preview — use **Improve with Prime** for depth.",
	}.get(str(source), "Build complete.")
	state.chat.append(
		ChatMessage(
			role="assistant",
			content=(
				f"Built **{state.app_config.title}** with {len(rows)} findings. {honesty} "
				f"Open **Preview** when ready — or keep chatting to refine. Use **Stop** if a job hangs."
			),
			source=str(source),
		)
	)
	save_checkpoint(state, "Initial build")
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


def start_approve_build(project_id: str) -> dict[str, Any]:
	"""Non-blocking: deepen with Prime (or full build if no preview yet)."""
	approve_plan(project_id)

	def target(_job: JobRecord) -> None:
		cur = load_state(project_id)
		if cur.deploy_url and (project_dir(project_id) / "app").exists() and _load_rows(project_id):
			deepen_with_prime(project_id)
		else:
			build_project(cur, run_prime=True)

	try:
		job = start_job(project_id, "build_run", label="Improve with Prime", target=target)
	except JobConflictError as exc:
		raise ValueError(str(exc)) from exc
	return {"job_id": job.id, "status": "running", **project_snapshot(project_id)}


def follow_up(project_id: str, message: str) -> ProjectState:
	"""Synchronous follow-up (tests). Prefer start_follow_up for API UI changes."""
	return _follow_up_impl(project_id, message)


def start_follow_up(project_id: str, message: str) -> dict[str, Any]:
	state = load_state(project_id)
	if state.phase == "plan":
		start_plan_chat(project_id, message)
		return project_snapshot(project_id)

	if is_ui_change_request(message):
		state.chat.append(ChatMessage(role="user", content=message, source="system"))
		state.design_brief = merge_notes_from_message(state.design_brief, message)
		write_brief(project_id, state.design_brief)
		save_state(state)

		def target(_job: JobRecord) -> None:
			_iterate_ui(project_id, message)

		try:
			job = start_job(project_id, "iterate_run", label="Iterating app", target=target)
		except JobConflictError as exc:
			raise ValueError(str(exc)) from exc
		return {"job_id": job.id, "status": "running", **project_snapshot(project_id)}

	# Q&A — sync short ask
	_follow_up_qa(project_id, message)
	return project_snapshot(project_id)


def _iterate_ui(project_id: str, message: str) -> None:
	state = load_state(project_id)
	save_checkpoint(state, f"Before: {message[:40]}")
	emit_event(project_id, "phase", label="Processing UI iterate", detail=message[:120], status="running")
	rows = _load_rows(project_id)
	app_dir = project_dir(project_id) / "app"
	write_brief(project_id, state.design_brief)
	apply_brief_css_tokens(app_dir, state.design_brief)

	meta = prime_build_app(
		app_dir,
		f"{state.prompt}\n\nFollow-up: {message}",
		project_id=project_id,
		row_count=len(rows),
		delta_note=message,
		kind="iterate_run",
	)
	source = meta.get("source") or ("prime" if meta.get("ok") else "heuristic")
	if not meta.get("used") or not meta.get("ok"):
		apply_follow_up(state, message)
		source = "heuristic" if not meta.get("used") else source
		state = load_state(project_id)

	state = load_state(project_id)
	state.app_config = infer_app_config(message, state.app_config)
	state.prime = {
		**state.prime,
		"source": source,
		"last_error": meta.get("error"),
		"status": "ok" if meta.get("ok") or not meta.get("used") else "error",
	}
	honesty = "Prime updated the app." if source == "prime" else "Applied heuristic refine (Prime unavailable)."
	state.chat.append(ChatMessage(role="assistant", content=f"{honesty} Preview refreshing…", source=source))
	state.status = "updating"
	save_state(state)
	url = start_preview(state, rows, app_dir=app_dir)
	state.deploy_url = url
	state.status = "ready"
	save_checkpoint(state, f"After: {message[:40]}")
	emit_event(project_id, "done", label="Preview updated", detail=url, status="done")
	save_state(state)


def _follow_up_qa(project_id: str, message: str) -> ProjectState:
	state = load_state(project_id)
	state.chat.append(ChatMessage(role="user", content=message, source="system"))
	rows = _load_rows(project_id)
	summary = write_summary(rows, state.prompt) if rows else ""
	emit_event(project_id, "think", label="Prime follow-up Q&A", status="running")
	reply = prime_follow_up(project_dir(project_id), state, message, summary, project_id=project_id)
	if reply:
		state.chat.append(ChatMessage(role="assistant", content=reply, source="prime"))
		state.prime["source"] = "prime"
	else:
		apply_follow_up(state, message)
		state = load_state(project_id)
		if state.chat and state.chat[-1].role == "assistant":
			state.chat[-1].source = "heuristic"
		state.prime["source"] = "heuristic"
	emit_event(project_id, "think", label="Follow-up answered", status="done")
	save_state(state)
	return state


def _follow_up_impl(project_id: str, message: str) -> ProjectState:
	state = load_state(project_id)
	if is_ui_change_request(message):
		state.chat.append(ChatMessage(role="user", content=message, source="system"))
		state.design_brief = merge_notes_from_message(state.design_brief, message)
		write_brief(project_id, state.design_brief)
		save_state(state)
		_iterate_ui(project_id, message)
		return load_state(project_id)
	return _follow_up_qa(project_id, message)


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


def approve_deploy(project_id: str) -> ProjectState:
	state = load_state(project_id)
	if state.gates_status != "pass":
		raise ValueError("Gates must pass before deploy")
	state.deployed = True
	state.status = "deployed"
	if not state.deploy_url and state.preview_port:
		state.deploy_url = f"http://127.0.0.1:{state.preview_port}"
	save_state(state)
	return state


def project_snapshot(project_id: str) -> dict:
	from .jobs import get_job

	state = load_state(project_id)
	# Heal ghost "running" after process restart (in-memory job gone)
	live = get_job(project_id)
	job = dict(state.job or {})
	if job.get("status") in ("running", "settling") and live is None:
		job["status"] = "idle"
		state.job = job
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
		"project": state.to_dict(),
		"preview_data": preview,
		"preview_url": state.deploy_url,
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
