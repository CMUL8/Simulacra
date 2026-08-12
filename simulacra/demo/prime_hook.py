"""Prime Agent chat envelope — main product chat. Simulacra observes structured requests."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from simulacra.env import load_dotenv

from .design_brief import brief_to_prime_block
from .events import emit_event
from .prime_session import prime_ask
from .runs import AppConfig, ProjectState

ChatRequest = Literal["await_user", "build", "iterate", "research"]
VALID_REQUESTS = frozenset({"await_user", "build", "iterate", "research"})


@dataclass
class PrimeBuildMeta:
	used: bool = False
	session_id: str | None = None
	model: str | None = None
	error: str | None = None
	source: str = "heuristic"


@dataclass
class PrimeChatTurn:
	"""Structured result of one Prime chat turn — Simulacra observes, does not invent."""

	reply: str | None = None
	title: str | None = None
	subtitle: str | None = None
	request: ChatRequest = "await_user"
	brief: str | None = None
	meta: PrimeBuildMeta = field(default_factory=PrimeBuildMeta)

	@property
	def config(self) -> AppConfig | None:
		if not self.title and not self.subtitle:
			return None
		cfg = AppConfig()
		if self.title:
			cfg.title = self.title[:80]
		if self.subtitle:
			cfg.subtitle = self.subtitle[:120]
		return cfg


def prime_enabled() -> bool:
	load_dotenv()
	return os.environ.get("SIMULACRA_USE_PRIME", "").lower() in ("1", "true", "yes")


def prime_kwargs() -> dict[str, Any]:
	load_dotenv()
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


def _room_lines(preview: dict[str, Any]) -> str:
	try:
		from .sources import source_room_brief, source_room_lines

		room = preview.get("source_room") or source_room_brief(preview)
		return "\n".join(f"- {ln}" for ln in source_room_lines(room))
	except Exception:  # noqa: BLE001
		return f"- {preview.get('row_count', 0)} rows · {len(preview.get('files') or [])} files"


def _data_block(preview: dict[str, Any]) -> str:
	try:
		from .sources import DataProfile, sources_to_prime_block

		raw = preview.get("profile") or {}
		if not raw:
			return ""
		profile = DataProfile(**{k: v for k, v in raw.items() if k in DataProfile.__dataclass_fields__})
		return sources_to_prime_block(profile)
	except Exception:  # noqa: BLE001
		return ""


def _envelope_schema_block() -> str:
	return (
		"Reply with ONLY valid JSON (no markdown fences):\n"
		"{\n"
		'  "title": "short product name (optional on follow-ups)",\n'
		'  "subtitle": "one-line description (optional)",\n'
		'  "reply": "markdown for the user",\n'
		'  "request": "await_user | build | iterate | research",\n'
		'  "brief": "optional instruction for iterate or research"\n'
		"}\n\n"
		"request meanings:\n"
		"- await_user — keep talking; default\n"
		"- build — you are ready for the user to hit Build (do not claim Built)\n"
		"- iterate — edit the existing artifact (only if one already exists)\n"
		"- research — you want to gather/web material (say what you would do; "
		"do not invent finished research as fact)\n\n"
		"Reply rules:\n"
		"- Write for a product user, not a developer.\n"
		"- Do NOT list filenames, paths, or markdown tables of files in reply.\n"
		"- Summarize what you learned in prose; Simulacra shows sources in the data room.\n"
		"- Never invent vendor/risk UI sections unless the user asked for vendor risk.\n"
	)


def _recent_chat_block(state: ProjectState, *, limit: int = 10) -> str:
	"""Compact recent transcript so follow-ups stay coherent even if session resume is weak."""
	lines: list[str] = []
	for m in (state.chat or [])[-limit:]:
		role = (m.role or "?").upper()
		content = (m.content or "").strip().replace("\n", " ")
		if len(content) > 280:
			content = content[:277] + "…"
		if content:
			lines.append(f"{role}: {content}")
	if not lines:
		return "(no prior chat)"
	return "\n".join(lines)


def prime_chat_turn(
	cwd: Path,
	state: ProjectState,
	*,
	message: str | None = None,
	open_turn: bool = False,
	project_id: str | None = None,
) -> PrimeChatTurn:
	"""Single Prime chat turn for the product. Main chat = this helper.

	Always uses the same session name + session_dir so Prime's RLM worker can
	resume context across the ~10–12 follow-ups a normal user sends.
	"""
	if not prime_enabled() or not (project_id or state.id):
		return PrimeChatTurn(meta=PrimeBuildMeta(used=False, source="heuristic"))

	pid = project_id or state.id
	preview = state.plan_preview or {}
	summary = str(preview.get("summary") or "")
	room_lines = _room_lines(preview)
	has_artifact = state.phase in ("ready", "build") and bool(
		state.deploy_url or (cwd / "app" / "package.json").exists()
	)

	phase_note = (
		f"Project phase: {state.phase}. "
		+ (
			"An artifact/preview already exists — you may request iterate.\n"
			if has_artifact
			else "No Built artifact yet — do not request iterate; use await_user, build, or research.\n"
		)
	)

	research_note = (
		"When researching, write files under `work/research/` "
		"(markdown/json/csv). Simulacra promotes them into the data room automatically — "
		"do not tell the user filenames or dump a file table in chat.\n"
	)

	mismatch = (state.prime or {}).get("topic_mismatch") or {}
	topic_note = ""
	if isinstance(mismatch, dict) and mismatch.get("reason") and not (state.prime or {}).get(
		"topic_mismatch_announced"
	):
		topic_note = (
			f"System note (soft — do not hard-block): {mismatch.get('reason')}\n"
			"Be honest; suggest upload, sample swap, or research for their topic.\n"
		)

	# One stable session name per project — RLM resume within the project,
	# never shared across users (session_dir is already per-project).
	session_name = f"chat-{pid}"

	if open_turn:
		design = brief_to_prime_block(state.design_brief or {})
		data_block = _data_block(preview)
		role = (
			"You are the Simulacra agent, live in the main chat with the user.\n"
			"This is the opening turn after create. They will steer you across many follow-ups.\n"
			"Do NOT claim you have built anything. Do not write app code in this turn.\n"
			"Use your persistent session/RLM memory — later turns will resume this conversation.\n"
		)
		user_bit = f"User request:\n{state.prompt}\n\nGoal (if any):\n{state.goal or '(none)'}\n"
		context_bit = (
			f"Data room inventory:\n{room_lines}\n\n"
			f"Source summary:\n{summary[:1800]}\n\n"
			f"Stats: {preview.get('row_count', 0)} rows, "
			f"{preview.get('high_risk', 0)} high-risk, "
			f"{len(preview.get('vendors') or [])} vendors, "
			f"{len(preview.get('files') or [])} files.\n\n"
			f"{data_block}\n\n"
			f"{design}\n"
		)
		ask_timeout = 180.0
	else:
		# Slim follow-ups: rely on session memory + short recent transcript, not a full dump.
		role = (
			"You are the Simulacra agent continuing the SAME chat session with the user.\n"
			"Resume prior context from your session/RLM memory. Do not restart from scratch.\n"
			"They steer you — sources, sample pack, research, scope, tone, UI edits.\n"
			"Do NOT force a vendor dashboard unless they want that.\n"
			"Do not write app code in this turn; request iterate/build instead.\n"
		)
		user_bit = (
			f"Product so far: {state.app_config.title} — {state.app_config.subtitle}\n"
			f"Original ask: {state.prompt[:400]}\n\n"
			f"Recent chat:\n{_recent_chat_block(state)}\n\n"
			f"Latest user message:\n{message or ''}\n"
		)
		context_bit = (
			f"Data room inventory (current):\n{room_lines}\n\n"
			f"Rows={preview.get('row_count', 0)} high={preview.get('high_risk', 0)} "
			f"vendors={len(preview.get('vendors') or [])} files={len(preview.get('files') or [])}\n"
		)
		ask_timeout = 240.0

	prime_prompt = (
		f"{role}\n"
		f"{phase_note}\n"
		f"{research_note}"
		f"{topic_note}"
		"Be honest about the data room. Never silently pretend unrelated attached rows "
		"are about their topic. Never claim Built until Simulacra has actually built.\n\n"
		f"{user_bit}\n"
		f"{context_bit}\n"
		f"{_envelope_schema_block()}"
	)

	text, meta = prime_ask(
		pid,
		cwd=cwd,
		prompt=prime_prompt,
		name=session_name,
		timeout=ask_timeout,
	)
	out_meta = PrimeBuildMeta(
		used=True,
		session_id=meta.get("session_id"),
		model=meta.get("model"),
		error=meta.get("error"),
		source="prime" if text else "error",
	)
	if meta.get("error"):
		emit_event(pid, "error", label="Prime chat error", detail=str(meta["error"])[:200], status="fail")

	turn = _parse_chat_envelope(text)
	turn.meta = out_meta
	if not turn.reply and text and text.strip():
		turn.reply = text.strip()
		if out_meta.source == "prime" and not turn.request:
			turn.request = "await_user"
	if turn.request == "iterate" and not has_artifact:
		turn.request = "await_user"
		if turn.brief and turn.reply:
			turn.reply = f"{turn.reply}\n\n_(Build first — then I can apply edits.)_"
	if out_meta.source == "error" and not turn.reply:
		out_meta.source = "error"
	elif turn.reply and out_meta.source != "error":
		out_meta.source = "prime"
	turn.meta = out_meta
	return turn


def _parse_chat_envelope(text: str | None) -> PrimeChatTurn:
	turn = PrimeChatTurn()
	if not text:
		return turn
	raw = text.strip()
	fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
	if fence:
		raw = fence.group(1).strip()
	match = re.search(r"\{[\s\S]*\}", raw)
	if not match:
		turn.reply = text.strip()
		return turn
	try:
		data = json.loads(match.group())
	except json.JSONDecodeError:
		turn.reply = text.strip()
		return turn

	if title := data.get("title"):
		turn.title = str(title)[:80]
	if subtitle := data.get("subtitle"):
		turn.subtitle = str(subtitle)[:120]
	if reply := data.get("reply"):
		turn.reply = str(reply).strip()
	req = str(data.get("request") or "await_user").strip().lower()
	turn.request = req if req in VALID_REQUESTS else "await_user"  # type: ignore[assignment]
	if brief := data.get("brief"):
		turn.brief = str(brief).strip()[:2000] or None
	return turn


# ── Compat wrappers (tests / older call sites) ───────────────────────


def prime_open_plan(
	cwd: Path,
	state: ProjectState,
	*,
	summary: str,
	project_id: str | None = None,
) -> tuple[AppConfig | None, str | None, PrimeBuildMeta]:
	"""Compat: opening turn via prime_chat_turn."""
	_ = summary
	turn = prime_chat_turn(cwd, state, open_turn=True, project_id=project_id)
	return turn.config, turn.reply, turn.meta


def prime_plan_chat(
	cwd: Path, state: ProjectState, message: str, *, project_id: str | None = None
) -> str | None:
	"""Compat: returns reply text only."""
	turn = prime_chat_turn(cwd, state, message=message, project_id=project_id)
	return turn.reply


def prime_follow_up(
	cwd: Path,
	state: ProjectState,
	message: str,
	rows_summary: str,
	*,
	project_id: str | None = None,
) -> str | None:
	"""Compat: Q&A-shaped follow-up via the same envelope."""
	_ = rows_summary
	turn = prime_chat_turn(cwd, state, message=message, project_id=project_id)
	return turn.reply


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
	"""Deprecated for product chat routing — kept for tests/scripts."""
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
	return not is_question_only(message)
