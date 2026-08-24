"""Persistent Prime Agent sessions for product path (not smoke tests)."""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

from simulacra.agent import Agent, RunOptions
from simulacra.env import load_dotenv

from .events import emit_event, emit_prime_event, last_event
from .jobs import JobCancelled, check_bounds, note_event, register_abort
from .runs import load_state, project_dir, save_state

log = logging.getLogger("simulacra.prime_session")

_WRITE_TOOL_HINTS = ("write", "edit", "replace", "patch", "create_file", "apply_diff", "str_replace")


def _rpc_tool_name(raw: dict[str, Any]) -> str:
	"""Match Prime RPC fields: toolName first, then legacy aliases / nesting."""
	for key in ("toolName", "tool_name", "tool", "name"):
		v = raw.get(key)
		if isinstance(v, str) and v.strip():
			return v.strip()
	for nest in ("toolCall", "tool_call", "call", "data"):
		nested = raw.get(nest)
		if isinstance(nested, dict):
			found = _rpc_tool_name(nested)
			if found:
				return found
	return ""


def _count_write_tools(events: list[dict[str, Any]]) -> int:
	n = 0
	for e in events:
		kind = e.get("type") or ""
		if kind not in ("tool_execution_end", "tool_execution_start", "tool_use"):
			continue
		tool = _rpc_tool_name(e).lower()
		if any(h in tool for h in _WRITE_TOOL_HINTS):
			n += 1
	return n


def _prime_enabled() -> bool:
	from .prime_hook import prime_enabled

	return prime_enabled()


def _prime_kwargs() -> dict[str, Any]:
	from .prime_hook import prime_kwargs

	return prime_kwargs()


def session_dir_for(project_id: str, chat_id: str | None = None) -> Path:
	base = project_dir(project_id) / "work" / "prime-session"
	if chat_id:
		path = base / chat_id
	else:
		# Prefer active chat dir when available
		try:
			from .runs import get_active_thread, load_state

			path = base / get_active_thread(load_state(project_id)).id
		except Exception:  # noqa: BLE001
			path = base
	path.mkdir(parents=True, exist_ok=True)
	return path


def _on_event(project_id: str):
	def handler(raw: dict[str, Any]) -> None:
		tool = _rpc_tool_name(raw)
		sig = None
		if raw.get("type") == "tool_execution_start" and tool:
			args = raw.get("args") or raw.get("input") or raw.get("arguments") or ""
			sig = f"{tool}:{str(args)[:80]}"
		note_event(project_id, tool_sig=sig)
		emit_prime_event(project_id, raw)
		try:
			check_bounds(project_id)
		except JobCancelled:
			# Abort is triggered by job manager / outer loop
			pass

	return handler


def _run_coro(coro):
	"""Run async coroutine from sync pipeline threads."""
	try:
		loop = asyncio.get_running_loop()
	except RuntimeError:
		loop = None
	if loop and loop.is_running():
		# Nested: run in a fresh loop on a dedicated thread
		result: dict[str, Any] = {}
		error: list[BaseException] = []

		def target() -> None:
			try:
				result["value"] = asyncio.run(coro)
			except BaseException as exc:  # noqa: BLE001
				error.append(exc)

		t = threading.Thread(target=target, daemon=True)
		t.start()
		t.join()
		if error:
			raise error[0]
		return result.get("value")
	return asyncio.run(coro)


async def _ask_async(
	project_id: str,
	*,
	cwd: Path,
	prompt: str,
	name: str,
	timeout: float,
	ephemeral_session: bool = False,
) -> tuple[str | None, dict[str, Any]]:
	load_dotenv()
	meta: dict[str, Any] = {"used": True, "ok": False, "source": "prime"}
	if not _prime_enabled():
		return None, {"used": False, "ok": False, "source": "heuristic"}

	agent = Agent(
		cwd=cwd,
		no_session=ephemeral_session,
		session_dir=None if ephemeral_session else session_dir_for(project_id),
		name=None if ephemeral_session else name,
		on_event=_on_event(project_id),
		**_prime_kwargs(),
	)

	async def abort_coro() -> None:
		try:
			await agent.abort()
		except Exception:  # noqa: BLE001
			pass

	def abort_hook() -> None:
		try:
			asyncio.run_coroutine_threadsafe(abort_coro(), asyncio.get_event_loop())
		except Exception:
			# Best effort from another thread
			try:
				_run_coro(abort_coro())
			except Exception:  # noqa: BLE001
				pass

	register_abort(project_id, abort_hook)

	# Keep job alive during long think stretches (no tools) — RLM chat can take minutes.
	stop_hb = threading.Event()

	def _heartbeat() -> None:
		while not stop_hb.wait(20.0):
			try:
				# Keep job liveness even when we skip a duplicate Thinking line.
				note_event(project_id)
				prev = last_event(project_id)
				# Don't stack "Thinking" every 20s — refresh only if something else ran.
				if (
					prev
					and prev.get("type") == "think"
					and str(prev.get("label") or "").strip().lower() in ("thinking", "thinking…", "thinking...")
					and prev.get("status") == "running"
				):
					continue
				emit_event(project_id, "think", label="Thinking", status="running")
			except Exception:  # noqa: BLE001
				pass

	hb = threading.Thread(target=_heartbeat, name=f"prime-hb-{project_id[:8]}", daemon=True)
	hb.start()

	try:
		await agent.start()
		state = await agent.state()
		meta["session_id"] = state.get("sessionId") or state.get("session_id")
		meta["model"] = state.get("model")
		check_bounds(project_id)
		text = await agent.ask(prompt, timeout=timeout)
		meta["ok"] = bool(text)
		meta["text"] = text
		if not ephemeral_session:
			_save_prime_meta(project_id, meta)
		return text, meta
	except Exception as exc:  # noqa: BLE001
		meta["error"] = str(exc)[:300]
		meta["source"] = "error"
		emit_event(project_id, "error", label="Ask failed", detail=meta["error"], status="fail")
		if not ephemeral_session:
			_save_prime_meta(project_id, meta)
		return None, meta
	finally:
		stop_hb.set()
		try:
			await agent.stop()
		except Exception:  # noqa: BLE001
			pass


async def _run_async(
	project_id: str,
	*,
	cwd: Path,
	prompt: str,
	name: str,
	timeout: float,
) -> dict[str, Any]:
	load_dotenv()
	meta: dict[str, Any] = {"used": True, "ok": False, "source": "prime", "events": 0}
	if not _prime_enabled():
		return {"used": False, "ok": False, "source": "heuristic"}

	session_dir = session_dir_for(project_id)
	agent = Agent(
		cwd=cwd,
		no_session=False,
		session_dir=session_dir,
		name=name,
		on_event=_on_event(project_id),
		**_prime_kwargs(),
	)

	loop = asyncio.get_event_loop()

	async def abort_coro() -> None:
		try:
			await agent.abort()
		except Exception:  # noqa: BLE001
			pass

	def abort_hook() -> None:
		fut = asyncio.run_coroutine_threadsafe(abort_coro(), loop)
		try:
			fut.result(timeout=5)
		except Exception:  # noqa: BLE001
			pass

	register_abort(project_id, abort_hook)

	try:
		await agent.start()
		state = await agent.state()
		meta["session_id"] = state.get("sessionId") or state.get("session_id")
		meta["model"] = state.get("model")
		_save_prime_meta(project_id, meta)
		check_bounds(project_id)
		result = await agent.run(prompt, RunOptions(timeout=timeout, collect_events=True))
		meta["events"] = len(result.events)
		meta["write_tools"] = _count_write_tools(result.events)
		meta["tool_calls"] = len(result.tool_calls)
		# Run completed — durable success is decided by builder via App.tsx fingerprint
		meta["ok"] = True
		meta["reply"] = (result.text or "")[:500]
		_save_prime_meta(project_id, meta)
		return meta
	except Exception as exc:  # noqa: BLE001
		meta["error"] = str(exc)[:300]
		meta["source"] = "error"
		emit_event(project_id, "error", label="Build failed", detail=meta["error"], status="fail")
		_save_prime_meta(project_id, meta)
		return meta
	finally:
		try:
			await agent.stop()
		except Exception:  # noqa: BLE001
			pass


def _save_prime_meta(project_id: str, meta: dict[str, Any]) -> None:
	try:
		state = load_state(project_id)
	except FileNotFoundError:
		return
	# Preserve observer fields (request/brief) set by chat turn — only patch session health.
	prev = dict(state.prime or {})
	state.prime = {
		**prev,
		"session_id": meta.get("session_id") or prev.get("session_id"),
		"session_dir": str(session_dir_for(project_id)),
		"model": meta.get("model") or prev.get("model"),
		"source": meta.get("source") or prev.get("source") or "prime",
		"last_error": meta.get("error"),
		"status": "ok" if meta.get("ok") else ("error" if meta.get("error") else prev.get("status")),
	}
	save_state(state)


def prime_ask(
	project_id: str,
	*,
	cwd: Path,
	prompt: str,
	name: str | None = "simulacra",
	timeout: float = 240.0,
	ephemeral_session: bool = False,
) -> tuple[str | None, dict[str, Any]]:
	return _run_coro(_ask_async(
		project_id, cwd=cwd, prompt=prompt, name=name or "simulacra", timeout=timeout,
		ephemeral_session=ephemeral_session,
	))


def prime_run(
	project_id: str,
	*,
	cwd: Path,
	prompt: str,
	name: str = "simulacra-builder",
	timeout: float = 240.0,
) -> dict[str, Any]:
	return _run_coro(_run_async(project_id, cwd=cwd, prompt=prompt, name=name, timeout=timeout))
