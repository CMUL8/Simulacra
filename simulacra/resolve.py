"""Locate a runnable Prime Agent binary or source launcher."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def _repo_root() -> Path:
	# simulacra/resolve.py -> Simulacra/
	return Path(__file__).resolve().parent.parent


def resolve_prime_agent(
	explicit: str | Path | None = None,
	*,
	prefer_source: bool = True,
) -> list[str]:
	"""
	Return an argv prefix that launches Prime Agent.

	Resolution order:
	1. ``explicit`` argument
	2. ``PRIME_AGENT_BIN`` environment variable
	3. Local clone ``prime-agent/prime-agent.sh`` (when prefer_source)
	4. ``prime-agent`` on PATH
	"""
	candidates: list[str | Path] = []
	if explicit is not None:
		candidates.append(explicit)

	env_bin = os.environ.get("PRIME_AGENT_BIN")
	if env_bin:
		candidates.append(env_bin)

	source_launcher = _repo_root() / "prime-agent" / "prime-agent.sh"
	if prefer_source and source_launcher.is_file():
		candidates.append(source_launcher)

	path_bin = shutil.which("prime-agent")
	if path_bin:
		candidates.append(path_bin)

	for candidate in candidates:
		path = Path(candidate).expanduser()
		if path.is_file():
			resolved = str(path.resolve())
			if resolved.endswith(".sh") or path.name == "prime-agent.sh":
				return ["bash", resolved]
			return [resolved]
		# Allow bare command names already on PATH when passed explicitly.
		which = shutil.which(str(candidate))
		if which:
			return [which]

	raise FileNotFoundError(
		"Could not find Prime Agent. Install it, set PRIME_AGENT_BIN, "
		"or keep a clone at ./prime-agent."
	)
