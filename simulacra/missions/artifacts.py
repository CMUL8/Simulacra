"""Descriptor-relative Mission artifact reads (never trust a provider path)."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


def artifact_bytes(workspace: str | Path, artifact_ref: str) -> bytes:
    relative = Path(artifact_ref)
    if (relative.is_absolute() or not relative.parts or any(
        part in {"", ".", ".."} or any(ord(char) < 32 for char in part)
        for part in relative.parts
    )):
        raise ValueError("artifact_ref must be a relative project path")
    root = Path(workspace).resolve(strict=True)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    current_fd = root_fd
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        for part in relative.parts[:-1]:
            child = os.open(part, flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = child
        fd = os.open(relative.parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARTIFACT_BYTES:
                raise ValueError("artifact must be a regular file no larger than 16 MiB")
            parts: list[bytes] = []
            remaining = info.st_size
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    raise ValueError("artifact changed while being read")
                parts.append(chunk); remaining -= len(chunk)
            if os.read(fd, 1):
                raise ValueError("artifact changed while being read")
            return b"".join(parts)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("artifact_ref must name a regular non-symlink project file") from exc
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


def artifact_evidence(workspace: str | Path, artifact_ref: str) -> tuple[bytes, dict[str, object]]:
    value = artifact_bytes(workspace, artifact_ref)
    return value, {"artifact_ref": artifact_ref, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
