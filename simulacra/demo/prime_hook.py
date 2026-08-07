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
	return {
		"provider": os.environ.get("SIMULACRA_PRIME_PROVIDER", "openrouter"),
		"model": os.environ.get("SIMULACRA_PRIME_MODEL", "anthropic/claude-3-haiku"),
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


def prime_plan_chat(
	cwd: Path, state: ProjectState, message: str, *, project_id: str | None = None
) -> str | None:
	if not prime_enabled():
		return None
	pid = project_id or state.id
	preview = state.plan_preview
	design = brief_to_prime_block(state.design_brief or {})
	prime_prompt = (
		"You are Simulacra in PLAN mode (read-only). Help the user explore their data room "
		"and refine requirements. Do NOT claim to have built anything. "
		"You may propose design_brief changes; do not write app code.\n\n"
		f"User goal/prompt:\n{state.prompt}\n\n"
		f"Data preview: {json.dumps(preview, default=str)[:2000]}\n\n"
		f"{design}\n\n"
		f"User message:\n{message}\n\n"
		"Reply concisely in markdown. Mention integration control layer if asked about security."
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


def is_ui_change_request(message: str) -> bool:
	lower = message.lower()
	ui_verbs = (
		"add",
		"remove",
		"change",
		"make",
		"update",
		"redesign",
		"restyle",
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
	)
	question = lower.strip().endswith("?") or lower.startswith(
		("what", "why", "how many", "which", "who", "explain", "tell me")
	)
	if question and not any(v in lower for v in ("add", "change", "make", "update", "show")):
		return False
	return any(v in lower for v in ui_verbs)
