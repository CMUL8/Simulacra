"""Parallel Prime Agent pool for multi-agent / population-style runs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .agent import Agent, AgentResult, RunOptions


@dataclass
class PoolTask:
	"""One prompt assigned to one pooled agent."""

	prompt: str
	agent_name: str | None = None
	cwd: str | Path | None = None
	metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PoolResult:
	task: PoolTask
	result: AgentResult | None = None
	error: str | None = None

	@property
	def ok(self) -> bool:
		return self.error is None and self.result is not None


class AgentPool:
	"""
	Run many Prime Agent sessions with bounded concurrency.

	Useful when each persona / worktree needs an isolated agent process.
	"""

	def __init__(
		self,
		*,
		concurrency: int = 4,
		provider: str | None = None,
		model: str | None = None,
		bin: str | Path | None = None,
		env: Mapping[str, str] | None = None,
		cwd: str | Path | None = None,
		no_session: bool = True,
		prefer_source: bool = True,
		extra_args: list[str] | None = None,
		timeout: float = 600.0,
		on_event: Callable[[str, dict[str, Any]], Any] | None = None,
	) -> None:
		if concurrency < 1:
			raise ValueError("concurrency must be >= 1")
		self.concurrency = concurrency
		self.provider = provider
		self.model = model
		self.bin = bin
		self.env = env
		self.cwd = cwd
		self.no_session = no_session
		self.prefer_source = prefer_source
		self.extra_args = list(extra_args or [])
		self.timeout = timeout
		self.on_event = on_event

	async def map(self, tasks: Sequence[PoolTask | str]) -> list[PoolResult]:
		normalized = [
			task if isinstance(task, PoolTask) else PoolTask(prompt=task) for task in tasks
		]
		semaphore = asyncio.Semaphore(self.concurrency)
		results: list[PoolResult | None] = [None] * len(normalized)

		async def _run_one(index: int, task: PoolTask) -> None:
			async with semaphore:
				name = task.agent_name or f"agent-{index + 1}"
				event_cb = None
				if self.on_event is not None:
					cb = self.on_event

					def event_cb(event: dict[str, Any], _name: str = name) -> Any:
						return cb(_name, event)

				try:
					async with Agent(
						cwd=task.cwd or self.cwd,
						provider=self.provider,
						model=self.model,
						bin=self.bin,
						env=self.env,
						extra_args=self.extra_args,
						no_session=self.no_session,
						prefer_source=self.prefer_source,
						name=name,
						on_event=event_cb,
					) as agent:
						result = await agent.run(
							task.prompt,
							RunOptions(timeout=self.timeout, collect_events=True),
						)
						results[index] = PoolResult(task=task, result=result)
				except Exception as exc:
					results[index] = PoolResult(task=task, error=str(exc))

		await asyncio.gather(*(_run_one(i, t) for i, t in enumerate(normalized)))
		return [r if r is not None else PoolResult(task=normalized[i], error="unknown") for i, r in enumerate(results)]

	async def ask_many(self, prompts: Sequence[str]) -> list[str | None]:
		pool_results = await self.map(prompts)
		out: list[str | None] = []
		for item in pool_results:
			if item.result is None:
				out.append(None)
			else:
				out.append(item.result.text)
		return out
