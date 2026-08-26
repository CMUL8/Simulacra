from __future__ import annotations

import os
import platform
import hashlib
import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from simulacra.missions import landlock
from simulacra.harnesses.codex_provider import CodexProviderRoute, OPENAI_BASE_URL


def _launcher_module() -> dict[str, object]:
    return runpy.run_path(str(Path(__file__).parents[1] / "deploy/bin/cmul8-mission-sandbox"))


def test_launcher_rejects_manifest_hash_tamper_and_scrubs_environment(monkeypatch, tmp_path: Path):
    launcher = _launcher_module()
    manifest = tmp_path / "manifest.json"; raw = b"{}"; manifest.write_bytes(raw); manifest.chmod(0o600)
    assert launcher["_open_manifest"](manifest, hashlib.sha256(raw).hexdigest()) == {}
    with pytest.raises(SystemExit, match="hash mismatch"):
        launcher["_open_manifest"](manifest, "0" * 64)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("UNRELATED_SECRET", "do-not-inherit")
    route = CodexProviderRoute("openai", OPENAI_BASE_URL, "OPENAI_API_KEY")
    env = launcher["_child_environment"](temp_root=tmp_path / "temp", codex_home=tmp_path / "home", route=route)
    assert env["OPENAI_API_KEY"] == "test-key" and "UNRELATED_SECRET" not in env
    assert env["TMPDIR"] == str(tmp_path / "temp") and env["CODEX_HOME"] == str(tmp_path / "home")


class _Libc:
    def __init__(self, abi=3, fail_at=None): self.calls=[]; self.abi=abi; self.fail_at=fail_at
    def prctl(self, *args): self.calls.append(("prctl", args)); return -1 if self.fail_at == "prctl" else 0
    def syscall(self, *args):
        self.calls.append(("syscall", args))
        if args[0] == landlock.CREATE and args[1] == 0: return self.abi
        if self.fail_at == args[0]: return -1
        return 42


def _roots(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "project"; workspace.mkdir()
    read, write, temp, home = (workspace / "read", workspace / "write", tmp_path / "temp", tmp_path / "home")
    for path in (read, write, temp, home): path.mkdir()
    runtime = tmp_path / "opt" / "codex"; (runtime / "bin").mkdir(parents=True)
    executable = runtime / "bin" / "codex"; executable.write_text("x"); executable.chmod(0o555)
    monkeypatch.setattr(landlock, "_CODEX_RUNTIME_ROOT", runtime)
    monkeypatch.setattr(landlock, "_null_device", lambda: Path("/dev/null"))
    return workspace, read, write, runtime, executable, temp, home


def test_landlock_mocked_exact_directory_and_file_rules(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    workspace, read, write, runtime, executable, temp, home = _roots(monkeypatch, tmp_path)
    libc = _Libc(); closed=[]
    monkeypatch.setattr(os, "close", lambda fd: closed.append(fd))
    landlock.apply(workspace, [read], [write], runtime_root=runtime, executable=executable, temp_root=temp, codex_home=home, libc=libc)
    assert libc.calls[0] == ("syscall", (landlock.CREATE, 0, 0, landlock.VERSION))
    assert libc.calls[1][0] == "prctl" and libc.calls[-1][1][0] == landlock.RESTRICT
    rules = [call[1][3]._obj for call in libc.calls if call[0] == "syscall" and call[1][0] == landlock.ADD]
    assert all(rule.allowed_access & ~landlock.HANDLED == 0 for rule in rules)
    assert any(rule.allowed_access == landlock.READ for rule in rules)
    assert any(rule.allowed_access == landlock.WRITE for rule in rules)
    assert any(rule.allowed_access == landlock.READ_FILE for rule in rules) or not list(landlock._tls_dns_files())
    assert any(rule.allowed_access == (landlock.READ_FILE | landlock.WRITE_FILE) for rule in rules)
    assert 42 in closed


def test_landlock_regular_read_scope_gets_file_read_only(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    workspace, read, write, runtime, executable, temp, home = _roots(monkeypatch, tmp_path)
    source = read / "input.txt"; source.write_text("x")
    libc = _Libc(); monkeypatch.setattr(os, "close", lambda _fd: None)
    landlock.apply(workspace, [source], [write], runtime_root=runtime, executable=executable, temp_root=temp, codex_home=home, libc=libc)
    rules = [call[1][3]._obj.allowed_access for call in libc.calls if call[0] == "syscall" and call[1][0] == landlock.ADD]
    assert landlock.READ_FILE in rules and landlock.READ not in rules


@pytest.mark.parametrize("abi, failure", [(2, None), (3, landlock.ADD), (3, landlock.RESTRICT)])
def test_landlock_fail_closed(monkeypatch, tmp_path: Path, abi, failure):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(os, "close", lambda _fd: None)
    workspace, read, write, runtime, executable, temp, home = _roots(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError):
        landlock.apply(workspace, [read], [write], runtime_root=runtime, executable=executable, temp_root=temp, codex_home=home, libc=_Libc(abi, failure))


@pytest.mark.skipif(platform.system() != "Linux", reason="requires Linux Landlock kernel")
def test_landlock_linux_integration_denies_unlisted_file(tmp_path: Path):
    workspace = tmp_path / "workspace"; read = workspace / "read"; write = workspace / "write"
    temp, home = tmp_path / "temp", tmp_path / "home"
    for path in (read, write, temp, home): path.mkdir(parents=True, exist_ok=True)
    (read / "input.txt").write_text("allowed", encoding="utf-8")
    script = """
from pathlib import Path
import subprocess
import sys
from simulacra.missions import landlock
landlock._CODEX_RUNTIME_ROOT = Path('/usr')
try:
    landlock.apply(Path(sys.argv[1]), [Path(sys.argv[2])], [Path(sys.argv[3])], runtime_root=Path('/usr'), executable=Path(sys.executable).resolve(), temp_root=Path(sys.argv[4]), codex_home=Path(sys.argv[5]))
except RuntimeError as exc:
    if 'ABI unavailable' in str(exc):
        raise SystemExit(77)
    raise
assert Path(sys.argv[2], 'input.txt').read_text() == 'allowed'
Path(sys.argv[3], 'output.txt').write_text('written')
with open('/dev/null', 'r+b', buffering=0) as null:
    null.write(b'probe')
subprocess.run(['/usr/bin/true'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
try:
    Path('/etc/passwd').read_text()
except PermissionError:
    raise SystemExit(0)
raise SystemExit(1)
"""
    result = subprocess.run([sys.executable, "-c", script, str(workspace), str(read), str(write), str(temp), str(home)], text=True, capture_output=True)
    if result.returncode == 77:
        pytest.skip("Landlock ABI unavailable")
    assert result.returncode == 0, result.stderr
    assert (write / "output.txt").read_text(encoding="utf-8") == "written"
