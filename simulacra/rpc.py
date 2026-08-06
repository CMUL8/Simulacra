"""Async JSONL RPC client for Prime Agent ``--mode rpc``."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import RpcError, StartupError
from .errors import TimeoutError as AgentTimeoutError
from .resolve import resolve_prime_agent

EventListener = Callable[[dict[str, Any]], Awaitable[None] | None]


@dataclass
class RpcClientOptions:
	"""Launch options for a Prime Agent RPC process."""

	cwd: str | Path | None = None
	provider: str | None = None
	model: str | None = None
	bin: str | Path | None = None
	env: Mapping[str, str] | None = None
	extra_args: list[str] = field(default_factory=list)
	no_session: bool = False
	session_dir: str | Path | None = None
	prefer_source: bool = True


class RpcClient:
	"""
	Typed-ish JSONL client that speaks Prime Agent's RPC protocol.

	Commands are newline-delimited JSON objects on stdin.
	Responses and events arrive as newline-delimited JSON on stdout.
	"""

	def __init__(self, options: RpcClientOptions | None = None) -> None:
		self.options = options or RpcClientOptions()
		self._process: asyncio.subprocess.Process | None = None
		self._reader_task: asyncio.Task[None] | None = None
		self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
		self._listeners: list[EventListener] = []
		self._request_id = 0
		self._stderr = ""
		self._closed = asyncio.Event()

	@property
	def stderr(self) -> str:
		return self._stderr

	@property
	def running(self) -> bool:
		return self._process is not None and self._process.returncode is None

	def on_event(self, listener: EventListener) -> Callable[[], None]:
		self._listeners.append(listener)

		def unsubscribe() -> None:
			if listener in self._listeners:
				self._listeners.remove(listener)

		return unsubscribe

	async def start(self) -> None:
		if self._process is not None:
			raise StartupError("RPC client already started")

		argv = resolve_prime_agent(self.options.bin, prefer_source=self.options.prefer_source)
		argv = [*argv, "--mode", "rpc"]

		if self.options.provider:
			argv.extend(["--provider", self.options.provider])
		if self.options.model:
			argv.extend(["--model", self.options.model])
		if self.options.no_session:
			argv.append("--no-session")
		if self.options.session_dir is not None:
			argv.extend(["--session-dir", str(self.options.session_dir)])
		argv.extend(self.options.extra_args)

		env = {**os.environ, **(self.options.env or {})}
		cwd = str(self.options.cwd) if self.options.cwd is not None else None

		try:
			self._process = await asyncio.create_subprocess_exec(
				*argv,
				stdin=asyncio.subprocess.PIPE,
				stdout=asyncio.subprocess.PIPE,
				stderr=asyncio.subprocess.PIPE,
				cwd=cwd,
				env=env,
			)
		except FileNotFoundError as exc:
			raise StartupError(f"Failed to spawn Prime Agent: {exc}") from exc

		self._closed.clear()
		self._reader_task = asyncio.create_task(self._read_stdout())
		asyncio.create_task(self._read_stderr())

		# Brief settle so immediate crashes surface as startup failures.
		await asyncio.sleep(0.15)
		if self._process.returncode is not None:
			raise StartupError(
				f"Prime Agent exited immediately with code {self._process.returncode}. "
				f"Stderr: {self._stderr.strip() or '(empty)'}"
			)

	async def stop(self, *, timeout: float = 5.0) -> None:
		if self._process is None:
			return

		proc = self._process
		try:
			if proc.returncode is None and proc.stdin and not proc.stdin.is_closing():
				proc.stdin.close()
			if proc.returncode is None:
				proc.terminate()
				try:
					await asyncio.wait_for(proc.wait(), timeout=timeout)
				except asyncio.TimeoutError:
					proc.kill()
					await proc.wait()
		finally:
			if self._reader_task is not None:
				self._reader_task.cancel()
				try:
					await self._reader_task
				except asyncio.CancelledError:
					pass
				self._reader_task = None
			for fut in self._pending.values():
				if not fut.done():
					fut.set_exception(RpcError("RPC client stopped"))
			self._pending.clear()
			self._process = None
			self._closed.set()

	async def send(self, command: dict[str, Any], *, timeout: float = 30.0) -> dict[str, Any]:
		if self._process is None or self._process.stdin is None:
			raise RpcError("RPC client not started")

		self._request_id += 1
		req_id = f"req_{self._request_id}"
		payload = {**command, "id": req_id}
		loop = asyncio.get_running_loop()
		fut: asyncio.Future[dict[str, Any]] = loop.create_future()
		self._pending[req_id] = fut

		line = json.dumps(payload, separators=(",", ":")) + "\n"
		self._process.stdin.write(line.encode("utf-8"))
		await self._process.stdin.drain()

		try:
			response = await asyncio.wait_for(fut, timeout=timeout)
		except asyncio.TimeoutError as exc:
			self._pending.pop(req_id, None)
			raise AgentTimeoutError(
				f"Timeout waiting for response to {command.get('type')}. "
				f"Stderr: {self._stderr.strip() or '(empty)'}"
			) from exc

		if not response.get("success", False):
			raise RpcError(str(response.get("error") or f"Command failed: {command.get('type')}"))
		return response

	async def prompt(self, message: str, *, images: list[dict[str, Any]] | None = None) -> None:
		cmd: dict[str, Any] = {"type": "prompt", "message": message}
		if images:
			cmd["images"] = images
		await self.send(cmd)

	async def steer(self, message: str) -> None:
		await self.send({"type": "steer", "message": message})

	async def follow_up(self, message: str) -> None:
		await self.send({"type": "follow_up", "message": message})

	async def abort(self) -> None:
		await self.send({"type": "abort"})

	async def get_state(self) -> dict[str, Any]:
		response = await self.send({"type": "get_state"})
		data = response.get("data")
		return data if isinstance(data, dict) else {}

	async def get_last_assistant_text(self) -> str | None:
		response = await self.send({"type": "get_last_assistant_text"})
		data = response.get("data") or {}
		text = data.get("text") if isinstance(data, dict) else None
		return text if isinstance(text, str) else None

	async def get_messages(self) -> list[Any]:
		response = await self.send({"type": "get_messages"})
		data = response.get("data") or {}
		messages = data.get("messages") if isinstance(data, dict) else None
		return messages if isinstance(messages, list) else []

	async def set_model(self, provider: str, model_id: str) -> dict[str, Any]:
		response = await self.send({"type": "set_model", "provider": provider, "modelId": model_id})
		data = response.get("data")
		return data if isinstance(data, dict) else {}

	async def new_session(self, parent_session: str | None = None) -> dict[str, Any]:
		cmd: dict[str, Any] = {"type": "new_session"}
		if parent_session is not None:
			cmd["parentSession"] = parent_session
		response = await self.send(cmd)
		data = response.get("data")
		return data if isinstance(data, dict) else {}

	async def wait_for_idle(self, *, timeout: float = 600.0) -> dict[str, Any]:
		"""Block until an ``agent_end`` event arrives; return that event."""
		loop = asyncio.get_running_loop()
		fut: asyncio.Future[dict[str, Any]] = loop.create_future()

		def _listener(event: dict[str, Any]) -> None:
			if event.get("type") == "agent_end" and not fut.done():
				fut.set_result(event)

		unsubscribe = self.on_event(_listener)
		try:
			return await asyncio.wait_for(fut, timeout=timeout)
		except asyncio.TimeoutError as exc:
			raise AgentTimeoutError(
				f"Timeout waiting for agent idle. Stderr: {self._stderr.strip() or '(empty)'}"
			) from exc
		finally:
			unsubscribe()

	async def collect_events(self, *, timeout: float = 600.0) -> list[dict[str, Any]]:
		events: list[dict[str, Any]] = []
		loop = asyncio.get_running_loop()
		fut: asyncio.Future[None] = loop.create_future()

		def _listener(event: dict[str, Any]) -> None:
			events.append(event)
			if event.get("type") == "agent_end" and not fut.done():
				fut.set_result(None)

		unsubscribe = self.on_event(_listener)
		try:
			await asyncio.wait_for(fut, timeout=timeout)
			return events
		except asyncio.TimeoutError as exc:
			raise AgentTimeoutError(
				f"Timeout collecting events. Stderr: {self._stderr.strip() or '(empty)'}"
			) from exc
		finally:
			unsubscribe()

	async def prompt_and_wait(
		self,
		message: str,
		*,
		images: list[dict[str, Any]] | None = None,
		timeout: float = 600.0,
	) -> list[dict[str, Any]]:
		events_task = asyncio.create_task(self.collect_events(timeout=timeout))
		try:
			await self.prompt(message, images=images)
			return await events_task
		except Exception:
			events_task.cancel()
			raise

	async def _read_stdout(self) -> None:
		assert self._process is not None and self._process.stdout is not None
		buffer = ""
		try:
			while True:
				chunk = await self._process.stdout.read(65536)
				if not chunk:
					break
				buffer += chunk.decode("utf-8", errors="replace")
				while True:
					idx = buffer.find("\n")
					if idx == -1:
						break
					line = buffer[:idx]
					buffer = buffer[idx + 1 :]
					if line.endswith("\r"):
						line = line[:-1]
					if line:
						await self._handle_line(line)
			if buffer:
				await self._handle_line(buffer[:-1] if buffer.endswith("\r") else buffer)
		finally:
			self._closed.set()

	async def _read_stderr(self) -> None:
		assert self._process is not None and self._process.stderr is not None
		while True:
			chunk = await self._process.stderr.read(65536)
			if not chunk:
				break
			self._stderr += chunk.decode("utf-8", errors="replace")

	async def _handle_line(self, line: str) -> None:
		try:
			data = json.loads(line)
		except json.JSONDecodeError:
			return
		if not isinstance(data, dict):
			return

		if data.get("type") == "response" and data.get("id") in self._pending:
			fut = self._pending.pop(data["id"])
			if not fut.done():
				fut.set_result(data)
			return

		for listener in list(self._listeners):
			try:
				result = listener(data)
				if asyncio.iscoroutine(result):
					await result
			except Exception:
				# Listener failures must not break the RPC loop.
				pass
