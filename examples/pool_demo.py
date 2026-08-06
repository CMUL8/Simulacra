#!/usr/bin/env python3
"""Example: bounded parallel prompts via AgentPool."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from simulacra import AgentPool, PoolResult, PoolTask


async def main() -> None:
	pool = AgentPool(concurrency=2, cwd=ROOT, no_session=True)
	tasks = [
		PoolTask(prompt="Reply with exactly: alpha", agent_name="persona-a"),
		PoolTask(prompt="Reply with exactly: beta", agent_name="persona-b"),
		PoolTask(prompt="Reply with exactly: gamma", agent_name="persona-c"),
	]
	results: list[PoolResult] = await pool.map(tasks)
	for item in results:
		status = "ok" if item.ok else f"err={item.error}"
		text = item.result.text if item.result else None
		print(f"[{item.task.agent_name}] {status}: {text!r}")


if __name__ == "__main__":
	asyncio.run(main())
