"""Minimal CLI for smoke-testing the Simulacra wrapper."""

from __future__ import annotations

import argparse
import asyncio
import sys

from .agent import Agent
from .resolve import resolve_prime_agent


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog="simulacra", description="Prime Agent Python wrapper")
	sub = parser.add_subparsers(dest="command", required=True)

	which = sub.add_parser("which", help="Print resolved Prime Agent launcher")
	which.add_argument("--bin", default=None, help="Explicit binary/launcher path")

	run = sub.add_parser("run", help="Send one prompt via RPC and print the reply")
	run.add_argument("prompt", help="User prompt")
	run.add_argument("--cwd", default=".", help="Working directory for the agent")
	run.add_argument("--provider", default=None)
	run.add_argument("--model", default=None)
	run.add_argument("--bin", default=None)
	run.add_argument("--timeout", type=float, default=600.0)
	run.add_argument("--no-session", action="store_true")

	return parser


async def _run(args: argparse.Namespace) -> int:
	async with Agent(
		cwd=args.cwd,
		provider=args.provider,
		model=args.model,
		bin=args.bin,
		no_session=args.no_session,
	) as agent:
		text = await agent.ask(args.prompt, timeout=args.timeout)
		if text:
			print(text)
			return 0
		print("(no assistant text)", file=sys.stderr)
		return 1


def main(argv: list[str] | None = None) -> None:
	parser = build_parser()
	args = parser.parse_args(argv)

	if args.command == "which":
		print(" ".join(resolve_prime_agent(args.bin)))
		return

	if args.command == "run":
		raise SystemExit(asyncio.run(_run(args)))

	parser.error(f"unknown command: {args.command}")


if __name__ == "__main__":
	main()
