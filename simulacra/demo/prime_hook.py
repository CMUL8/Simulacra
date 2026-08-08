"""Optional Prime Agent hooks for plan chat, config, and follow-up Q&A."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simulacra.env import load_dotenv

from .design_brief import brief_to_prime_block
from .events import emit_event
from .prime_session import prime_ask
from .runs import AppConfig, ProjectState


@dataclass
class PrimeBuildMeta:
	used: bool = False
	session_id: str | None = None
	model: str | None = None
	error: str | None = None
	source: str = "heuristic"


def prime_enabled() -> bool:
	load_dotenv()
	return os.environ.get("SIMULACRA_USE_PRIME", "").lower() in ("1", "true", "yes")


def prime_kwargs() -> dict[str, Any]:
	load_dotenv()
	# OpenRouter + DeepSeek V4 Pro (coding/agent default).
	# Override with SIMULACRA_PRIME_MODEL / SIMULACRA_BUILD_MODEL if needed.
	# Auth is OPENROUTER_API_KEY — not the model id.
	model = (
		os.environ.get("SIMULACRA_BUILD_MODEL")
		or os.environ.get("SIMULACRA_PRIME_MODEL")
		or "deepseek/deepseek-v4-pro"
	)
	return {
		"provider": os.environ.get("SIMULACRA_PRIME_PROVIDER", "openrouter"),
		"model": model,
	}


def _parse_config_json(text: str | None) -> AppConfig | None:
	if not text:
		return None
	match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
	if not match:
		return None
	try:
		data = json.loads(match.group())
	except json.JSONDecodeError:
		return None
	cfg = AppConfig()
	if title := data.get("title"):
		cfg.title = str(title)[:80]
	if subtitle := data.get("subtitle"):
		cfg.subtitle = str(subtitle)[:120]
	group = data.get("group_by")
	if group in ("vendor", "theme", "risk_level"):
		cfg.group_by = group
	return cfg


def prime_infer_app_config(
	cwd: Path, prompt: str, summary: str, *, project_id: str | None = None
) -> tuple[AppConfig | None, PrimeBuildMeta]:
	"""Prefer folding config into build_run; kept as optional polish."""
	if not prime_enabled() or not project_id:
		return None, PrimeBuildMeta(used=False, source="heuristic")

	prime_prompt = (
		"You are configuring an internal data dashboard for enterprise users.\n\n"
		f"User prompt:\n{prompt}\n\n"
		f"Data summary:\n{summary[:1800]}\n\n"
		"Reply with ONLY valid JSON, no markdown:\n"
		'{"title": "short app name", "subtitle": "one line description", "group_by": null}'
	)
	text, meta = prime_ask(
		project_id,
		cwd=cwd,
		prompt=prime_prompt,
		name="simulacra-config",
		timeout=60.0,
	)
	out = PrimeBuildMeta(
		used=True,
		session_id=meta.get("session_id"),
		model=meta.get("model"),
		error=meta.get("error"),
		source="prime" if text else "error",
	)
	cfg = _parse_config_json(text)
	if cfg is None and text:
		out.error = out.error or "could not parse Prime JSON response"
	return cfg, out


def prime_open_plan(
	cwd: Path,
	state: ProjectState,
	*,
	summary: str,
	project_id: str | None = None,
) -> tuple[AppConfig | None, str | None, PrimeBuildMeta]:
	"""First-turn plan: hand the user request to Prime immediately (no dashboard bias)."""
	if not prime_enabled() or not (project_id or state.id):
		return None, None, PrimeBuildMeta(used=False, source="heuristic")

	pid = project_id or state.id
	preview = state.plan_preview or {}
	design = brief_to_prime_block(state.design_brief or {})
	data_block = ""
	try:
		from .sources import DataProfile, sources_to_prime_block

		raw = preview.get("profile") or {}
		if raw:
			profile = DataProfile(**{k: v for k, v in raw.items() if k in DataProfile.__dataclass_fields__})
			data_block = sources_to_prime_block(profile)
	except Exception:  # noqa: BLE001
		data_block = ""
	prime_prompt = (
		"You are Simulacra in PLAN mode. The user just started a project.\n"
		"Propose what to build based on THEIR request — not a generic data explorer.\n"
		"Honor intent: games, learning/quiz apps, dashboards, ops tools, etc.\n"
		"Do NOT claim you have built anything yet. Do not write app code.\n"
		"Source material is available for the app to draw from; mention it briefly.\n"
		"Design the proposal around the actual data profile/nuances below.\n\n"
		f"User request:\n{state.prompt}\n\n"
		f"Goal (if any):\n{state.goal or '(none)'}\n\n"
		f"Source summary:\n{summary[:2200]}\n\n"
		f"Stats: {preview.get('row_count', 0)} extracted rows, "
		f"{preview.get('high_risk', 0)} high-risk, "
		f"{len(preview.get('vendors') or [])} vendors, "
		f"{len(preview.get('files') or [])} files.\n\n"
		f"{data_block}\n\n"
		f"{design}\n\n"
		"Reply with ONLY valid JSON (no markdown fences):\n"
		"{\n"
		'  "title": "short product name matching the request",\n'
		'  "subtitle": "one-line description",\n'
		'  "reply": "markdown for the user: reflect their ask, propose the app, '
		"briefly note sources and data nuances, invite refine or Approve & Build\"\n"
		"}"
	)
	text, meta = prime_ask(
		pid,
		cwd=cwd,
		prompt=prime_prompt,
		name="simulacra-plan-open",
		timeout=90.0,
	)
	out = PrimeBuildMeta(
		used=True,
		session_id=meta.get("session_id"),
		model=meta.get("model"),
		error=meta.get("error"),
		source="prime" if text else "error",
	)
	if meta.get("error"):
		emit_event(pid, "error", label="Prime plan open error", detail=str(meta["error"])[:200], status="fail")
	cfg, reply = _parse_plan_open_json(text)
	if cfg is None and text:
		out.error = out.error or "could not parse Prime plan JSON"
		# If Prime returned prose, still use it as the chat reply
		if not reply and text.strip():
			reply = text.strip()
	return cfg, reply, out


def _parse_plan_open_json(text: str | None) -> tuple[AppConfig | None, str | None]:
	if not text:
		return None, None
	raw = text.strip()
	fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
	if fence:
		raw = fence.group(1).strip()
	match = re.search(r"\{[\s\S]*\}", raw)
	if not match:
		return None, text.strip() if text.strip() else None
	try:
		data = json.loads(match.group())
	except json.JSONDecodeError:
		return None, text.strip() if text.strip() else None
	cfg = AppConfig()
	if title := data.get("title"):
		cfg.title = str(title)[:80]
	if subtitle := data.get("subtitle"):
		cfg.subtitle = str(subtitle)[:120]
	reply = data.get("reply")
	reply_s = str(reply).strip() if reply else None
	if not cfg.title and not reply_s:
		return None, None
	return cfg, reply_s


def prime_plan_chat(
	cwd: Path, state: ProjectState, message: str, *, project_id: str | None = None
) -> str | None:
	if not prime_enabled():
		return None
	pid = project_id or state.id
	preview = state.plan_preview
	design = brief_to_prime_block(state.design_brief or {})
	prime_prompt = (
		"You are Simulacra in PLAN mode (read-only). Help refine what to build.\n"
		"Do NOT force a vendor dashboard or data explorer unless the user wants that.\n"
		"Match their product intent (game, learning tool, analytics, ops, etc.).\n"
		"Do NOT claim to have built anything. Do not write app code.\n"
		"You may suggest look-and-feel / design_brief tweaks.\n\n"
		f"Proposed app so far: {state.app_config.title} — {state.app_config.subtitle}\n"
		f"User goal/prompt:\n{state.prompt}\n\n"
		f"Data preview: {json.dumps(preview, default=str)[:2000]}\n\n"
		f"{design}\n\n"
		f"User message:\n{message}\n\n"
		"Reply concisely in markdown."
	)
	text, meta = prime_ask(
		pid,
		cwd=cwd,
		prompt=prime_prompt,
		name="simulacra-plan",
		timeout=90.0,
	)
	if meta.get("error"):
		emit_event(pid, "error", label="Prime plan error", detail=str(meta["error"])[:200], status="fail")
	return text


def prime_follow_up(
	cwd: Path,
	state: ProjectState,
	message: str,
	rows_summary: str,
	*,
	project_id: str | None = None,
) -> str | None:
	"""Q&A only — UI mutations go through prime_build_app."""
	if not prime_enabled():
		return None
	pid = project_id or state.id
	prime_prompt = (
		"You are Simulacra answering a question about an internal data app.\n"
		"Do NOT claim you edited files — this is Q&A only.\n\n"
		f"Current app: {state.app_config.title}\n"
		f"Config: {json.dumps(state.app_config.__dict__)}\n"
		f"Data: {rows_summary[:1200]}\n\n"
		f"User question:\n{message}\n\n"
		"Reply in 1-5 concise sentences."
	)
	text, _meta = prime_ask(
		pid,
		cwd=cwd,
		prompt=prime_prompt,
		name="simulacra-chat",
		timeout=90.0,
	)
	return text


def prime_meta_dict(meta: PrimeBuildMeta) -> dict[str, Any]:
	if not meta.used:
		return {"session_id": None, "model": "simulacra-demo-pipeline", "source": "heuristic"}
	out: dict[str, Any] = {
		"session_id": meta.session_id,
		"model": meta.model or "prime-agent",
		"source": meta.source or "prime",
	}
	if meta.error:
		out["error"] = meta.error
	return out


def is_question_only(message: str) -> bool:
	"""True when the user is asking, not directing the builder."""
	lower = message.lower().strip()
	if not lower:
		return False
	change_verbs = (
		"add",
		"remove",
		"change",
		"make",
		"update",
		"redesign",
		"restyle",
		"fix",
		"improve",
		"rebuild",
		"replace",
		"filter",
		"layout",
		"color",
		"dark",
		"light",
		"dense",
		"compact",
		"card",
		"table",
		"chart",
		"kpi",
		"title",
		"show",
		"hide",
		"move",
		"bigger",
		"smaller",
		"font",
		"accent",
		"rename",
		"please edit",
		"please change",
	)
	if any(v in lower for v in change_verbs):
		return False
	if lower.endswith("?"):
		return True
	return lower.startswith(
		("what", "why", "how many", "how much", "which", "who", "explain", "tell me", "can you explain")
	)


def is_ui_change_request(message: str) -> bool:
	"""Backward-compatible alias — prefer is_question_only inverted for routing."""
	return not is_question_only(message)
