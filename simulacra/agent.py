"""High-level Prime Agent session wrapper."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .rpc import RpcClient, RpcClientOptions


@dataclass
class RunOptions:
	"""Options for a single prompt/run."""

	timeout: float = 600.0
	images: list[dict[str, Any]] | None = None
	collect_events: bool = True


@dataclass
class AgentResult:
	"""Result of a completed agent turn."""

	text: str | None
	events: list[dict[str, Any]] = field(default_factory=list)
	messages: list[Any] = field(default_factory=list)

	@property
	def tool_calls(self) -> list[dict[str, Any]]:
		return [e for e in self.events if e.get("type") == "tool_execution_end"]


class Agent:
	"""
	Ergonomic async wrapper around a long-lived Prime Agent RPC session.

	Example::

	    async with Agent(cwd=".", provider="anthropic") as agent:
	        result = await agent.run("Summarize this repo")
	        print(result.text)
	"""

	def __init__(
		self,
		*,
		cwd: str | Path | None = None,
		provider: str | None = None,
		model: str | None = None,
		bin: str | Path | None = None,
		env: Mapping[str, str] | None = None,
		extra_args: list[str] | None = None,
		no_session: bool = False,
		session_dir: str | Path | None = None,
		prefer_source: bool = True,
		name: str | None = None,
		on_event: Callable[[dict[str, Any]], Any] | None = None,
	) -> None:
		self.name = name
		self._on_event = on_event
		self._client = RpcClient(
			RpcClientOptions(
				cwd=cwd,
				provider=provider,
				model=model,
				bin=bin,
				env=env,
				extra_args=list(extra_args or []),
				no_session=no_session,
				session_dir=session_dir,
				prefer_source=prefer_source,
			)
		)
		self._unsubscribe: Callable[[], None] | None = None

	@property
	def client(self) -> RpcClient:
		return self._client

	@property
	def stderr(self) -> str:
		return self._client.stderr

	async def start(self) -> None:
		await self._client.start()
		if self._on_event is not None:
			self._unsubscribe = self._client.on_event(self._on_event)
		if self.name:
			try:
				await self._client.send({"type": "set_session_name", "name": self.name})
			except Exception:
				# Naming is best-effort; older builds may not support it at startup.
				pass

	async def stop(self) -> None:
		if self._unsubscribe is not None:
			self._unsubscribe()
			self._unsubscribe = None
		await self._client.stop()

	async def __aenter__(self) -> Agent:
		await self.start()
		return self

	async def __aexit__(self, *exc: object) -> None:
		await self.stop()

	async def run(self, prompt: str, options: RunOptions | None = None) -> AgentResult:
		opts = options or RunOptions()
		events: list[dict[str, Any]] = []
		if opts.collect_events:
			events = await self._client.prompt_and_wait(
				prompt,
				images=opts.images,
				timeout=opts.timeout,
			)
		else:
			await self._client.prompt(prompt, images=opts.images)
			await self._client.wait_for_idle(timeout=opts.timeout)

		text = await self._client.get_last_assistant_text()
		messages = await self._client.get_messages()
		return AgentResult(text=text, events=events, messages=messages)

	async def ask(self, prompt: str, *, timeout: float = 600.0) -> str | None:
		"""Convenience: run a prompt and return only the final assistant text."""
		result = await self.run(prompt, RunOptions(timeout=timeout, collect_events=False))
		return result.text

	async def steer(self, message: str) -> None:
		await self._client.steer(message)

	async def follow_up(self, message: str) -> None:
		await self._client.follow_up(message)

	async def abort(self) -> None:
		await self._client.abort()

	async def state(self) -> dict[str, Any]:
		return await self._client.get_state()


@asynccontextmanager
async def open_agent(**kwargs: Any) -> AsyncIterator[Agent]:
	agent = Agent(**kwargs)
	await agent.start()
	try:
		yield agent
	finally:
		await agent.stop()
