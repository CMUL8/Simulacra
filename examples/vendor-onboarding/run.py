#!/usr/bin/env python3
"""Run the vendor-onboarding reference scenario and print its durable outcome."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from reference import run_scenario


def main() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("--state-dir", type=Path, help="Fresh directory for durable reference state")
	parser.add_argument("--fail-first-notification", action="store_true", help="Exercise action retry/backoff")
	args = parser.parse_args()
	state_dir = args.state_dir or Path(tempfile.mkdtemp(prefix="vendor-onboarding-"))
	result = run_scenario(state_dir, fail_first_notification=args.fail_first_notification)
	print(json.dumps({"state_dir": str(state_dir.resolve()), **result}, indent=2, sort_keys=True))
	return 0 if result["delivered"] else 1


if __name__ == "__main__":
	raise SystemExit(main())
