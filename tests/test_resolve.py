from __future__ import annotations

from pathlib import Path

from simulacra.resolve import resolve_prime_agent


def test_resolve_finds_local_clone() -> None:
	argv = resolve_prime_agent(prefer_source=True)
	assert len(argv) >= 1
	joined = " ".join(argv)
	assert "prime-agent" in joined


def test_resolve_explicit_script(tmp_path: Path) -> None:
	script = tmp_path / "prime-agent.sh"
	script.write_text("#!/bin/bash\necho hi\n")
	script.chmod(0o755)
	argv = resolve_prime_agent(script)
	assert argv[0] == "bash"
	assert argv[1] == str(script.resolve())
