"""Small fail-closed Linux Landlock boundary for a Mission executor turn."""
from __future__ import annotations

import ctypes
import os
import platform
import stat
from pathlib import Path
from typing import Iterable


CREATE, ADD, RESTRICT, VERSION = 444, 445, 446, 1
EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
_WRITE_MUTATIONS = sum(1 << bit for bit in range(4, 15))
READ = READ_FILE | READ_DIR
EXEC_READ = EXECUTE | READ
WRITE = READ | WRITE_FILE | _WRITE_MUTATIONS
HANDLED = WRITE | EXECUTE


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathRule(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32), ("reserved", ctypes.c_uint32)]


def _canonical(path: str | Path, *, directory: bool | None = None) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise RuntimeError("Landlock paths must be absolute")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise RuntimeError("Landlock root is unavailable") from exc
    if raw.is_symlink() or (directory is True and not resolved.is_dir()) or (directory is False and not resolved.is_file()):
        raise RuntimeError("unsafe Landlock root")
    return resolved


def _null_device() -> Path:
    """Return only the canonical null character device, never a broad /dev root."""
    path = Path("/dev/null")
    try:
        info = path.stat()
    except OSError as exc:
        raise RuntimeError("null device is unavailable") from exc
    if path.is_symlink() or not stat.S_ISCHR(info.st_mode) or os.major(info.st_rdev) != 1 or os.minor(info.st_rdev) != 3:
        raise RuntimeError("unsafe null device")
    return path


def _under(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def _add_rule(libc, ruleset_fd: int, path: Path, access: int) -> None:
    flags = getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        child = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("Landlock root cannot be opened") from exc
    try:
        rule = PathRule(access, child, 0)
        if libc.syscall(ADD, ruleset_fd, 1, ctypes.byref(rule), 0) < 0:
            raise RuntimeError("Landlock add rule failed")
    finally:
        os.close(child)


def _tls_dns_files() -> Iterable[Path]:
    # Exact files only: the Codex process may resolve DNS and validate TLS, but
    # it never receives a broad /etc rule.
    for name in ("/etc/resolv.conf", "/etc/hosts", "/etc/nsswitch.conf", "/etc/ssl/certs/ca-certificates.crt", "/etc/ssl/cert.pem"):
        path = Path(name)
        if path.is_file() and not path.is_symlink():
            yield path.resolve(strict=True)


def apply(
    workspace: Path,
    read_roots: list[str | Path],
    write_roots: list[str | Path],
    *,
    runtime_root: Path,
    executable: Path,
    temp_root: Path,
    executor_home: Path | None = None,
    codex_home: Path | None = None,
    libc=None,
) -> None:
    """Restrict the current process to exact Mission and executor roots.

    Runtime and TLS/DNS files are explicit.  Mission roots are constrained to
    the supplied workspace; temporary and executor state roots are separate,
    private directories under the worker control root.
    """
    if platform.system() != "Linux":
        raise RuntimeError("Landlock requires Linux")
    workspace = _canonical(workspace, directory=True)
    runtime_root = _canonical(runtime_root, directory=True)
    executable = _canonical(executable, directory=False)
    temp_root = _canonical(temp_root, directory=True)
    selected_home = executor_home or codex_home
    if selected_home is None:
        raise RuntimeError("executor state root is required")
    selected_home = _canonical(selected_home, directory=True)
    if not _under(runtime_root, executable):
        raise RuntimeError("unsafe executor runtime root")
    read_paths = [_canonical(item) for item in read_roots]
    write_paths = [_canonical(item) for item in write_roots]
    if any(not _under(workspace, item) for item in read_paths + write_paths):
        raise RuntimeError("Mission Landlock root escapes workspace")
    if _under(workspace, temp_root) or _under(workspace, selected_home) or _under(Path("/app/runs"), temp_root) or _under(Path("/app/runs"), selected_home):
        raise RuntimeError("unsafe Mission private root")
    libc = libc or ctypes.CDLL(None, use_errno=True)
    if libc.syscall(CREATE, 0, 0, VERSION) < 3:
        raise RuntimeError("Landlock ABI unavailable")
    if libc.prctl(38, 1, 0, 0, 0):
        raise RuntimeError("no_new_privs failed")
    ruleset = Ruleset(HANDLED)
    ruleset_fd = libc.syscall(CREATE, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
    if ruleset_fd < 0:
        raise RuntimeError("Landlock ruleset failed")
    try:
        roots: list[tuple[Path, int]] = [(Path("/usr"), EXEC_READ), (runtime_root, EXEC_READ)]
        roots += [(item, READ if item.is_dir() else READ_FILE) for item in read_paths]
        roots += [(item, WRITE) for item in write_paths]
        roots += [(temp_root, WRITE), (selected_home, WRITE)]
        # Codex 0.148 shell-tool children use Stdio::null().  Landlock needs
        # this one exact character device; directory traversal or any other
        # /dev node remains denied.
        roots += [(_null_device(), READ_FILE | WRITE_FILE)]
        roots += [(item, READ_FILE) for item in _tls_dns_files()]
        seen: set[tuple[str, int]] = set()
        for root, access in roots:
            resolved = _null_device() if root == Path("/dev/null") else _canonical(root, directory=None)
            key = (str(resolved), access)
            if key not in seen:
                seen.add(key)
                _add_rule(libc, ruleset_fd, resolved, access)
        if libc.syscall(RESTRICT, ruleset_fd, 0) < 0:
            raise RuntimeError("Landlock restrict failed")
    finally:
        os.close(ruleset_fd)
