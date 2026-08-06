#!/usr/bin/env python3
"""Smoke example: one prompt through the Simulacra wrapper."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from simulacra import Agent


async def main() -> None:
	prompt = " ".join(sys.argv[1:]) or "Reply with exactly: ok"
	async with Agent(cwd=ROOT, no_session=True) as agent:
		text = await agent.ask(prompt)
		print(text or "(empty)")


if __name__ == "__main__":
	asyncio.run(main())
