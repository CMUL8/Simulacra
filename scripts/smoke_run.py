#!/usr/bin/env python3
"""Minimal end-to-end smoke: fixture data room → build → gates → preview."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
	sys.path.insert(0, str(ROOT))

from simulacra.env import load_dotenv
from simulacra.demo.pipeline import approve_and_build, init_plan
from simulacra.demo.prime_hook import prime_enabled
from simulacra.demo.runs import create_project

load_dotenv()


def main() -> int:
	prompt = " ".join(sys.argv[1:]) or "A simple vendor risk table ranked by severity"
	use_prime = prime_enabled()
	print(f"==> Simulacra smoke run (prime={'on' if use_prime else 'off'})")
	print(f"    prompt: {prompt[:80]}{'…' if len(prompt) > 80 else ''}")

	state = create_project(prompt, use_fixture=True)
	print(f"==> project {state.id}")

	state = init_plan(state)
	print(f"==> plan phase: {state.phase}, rows preview: {state.plan_preview.get('row_count', 0)}")

	# Plan open runs as a background job — wait for Prime (or heuristic) reply
	import time

	from simulacra.demo.jobs import get_job
	from simulacra.demo.runs import load_state

	for _ in range(120):
		job = get_job(state.id)
		if job is None or job.status not in ("running", "settling"):
			break
		time.sleep(0.5)
	state = load_state(state.id)
	print(f"==> plan open source: {state.prime.get('source')}")

	state = approve_and_build(state.id)
	print(f"==> status: {state.status}")
	print(f"==> app: {state.app_config.title}")
	print(f"==> rows: {state.row_count}")
	print(f"==> preview: {state.deploy_url}")

	manifest = json.loads((ROOT / "runs" / state.id / "outputs" / "manifest.json").read_text())
	prime = manifest.get("prime", {})
	print(f"==> prime: {json.dumps(prime)}")

	gates = json.loads((ROOT / "runs" / state.id / "audit" / "gates.json").read_text())
	print(f"==> gates: {gates['status']}")

	if state.status != "ready":
		return 1
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
