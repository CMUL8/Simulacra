"""Strict image-baked registry for interchangeable Mission executors."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

_REGISTRY_FORMAT = "missions.executor-registry.v1"
_EXECUTOR_ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{1,63}$")
_MAX_REGISTRY_BYTES = 64 * 1024
_DOCUMENT_FIELDS = {"format", "executors"}
_ENTRY_FIELDS = {"id", "adapter", "protocol", "network_policy"}


class ExecutorRegistryError(ValueError):
    """The baked registry is malformed, ambiguous, or unsafe."""


@dataclass(frozen=True)
class ExecutorRegistryEntry:
    id: str
    adapter: str
    protocol: str
    runtime_root: Path | None
    executable: Path | None


@dataclass(frozen=True)
class ExecutorRegistry:
    format: str
    entries: tuple[ExecutorRegistryEntry, ...]


def _secure_file(path: Path, *, executable: bool, require_root_owned: bool) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExecutorRegistryError("runtime executable is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ExecutorRegistryError("runtime executable must be a regular non-symlink file")
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise ExecutorRegistryError("runtime files must not be group- or world-writable")
    if executable and not info.st_mode & stat.S_IXUSR:
        raise ExecutorRegistryError("runtime executable is not executable")
    if require_root_owned and info.st_uid != 0:
        raise ExecutorRegistryError("runtime files must be root-owned")


def _validated_runtime(
    registry_root: Path,
    identifier: str,
    *,
    require_root_owned: bool,
) -> tuple[Path, Path]:
    runtime = registry_root / identifier
    executable = runtime / "bin" / "mission-executor"
    try:
        resolved_root = runtime.resolve(strict=True)
        resolved_executable = executable.resolve(strict=True)
    except OSError as exc:
        raise ExecutorRegistryError("runtime executable is unavailable") from exc
    if runtime.is_symlink() or executable.is_symlink() or resolved_root != runtime or resolved_executable != executable:
        raise ExecutorRegistryError("runtime executable must use the conventional non-symlink path")
    if not runtime.is_dir() or executable.parent.parent != runtime:
        raise ExecutorRegistryError("runtime executable path is invalid")
    _secure_file(executable, executable=True, require_root_owned=require_root_owned)
    if require_root_owned:
        for directory in (registry_root, runtime, executable.parent):
            info = directory.stat()
            if info.st_uid != 0 or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                raise ExecutorRegistryError("runtime directories must be root-owned and immutable")
    return runtime, executable


def load_executor_registry(
    path: str | os.PathLike[str],
    *,
    verify_runtimes: bool = False,
    require_root_owned: bool = False,
) -> ExecutorRegistry:
    registry_path = Path(path)
    _secure_file(registry_path, executable=False, require_root_owned=require_root_owned)
    try:
        if registry_path.stat().st_size > _MAX_REGISTRY_BYTES:
            raise ExecutorRegistryError("executor registry is too large")
        value = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExecutorRegistryError("executor registry is not valid UTF-8 JSON") from exc
    if not isinstance(value, dict) or set(value) != _DOCUMENT_FIELDS:
        raise ExecutorRegistryError("executor registry has unknown or missing fields")
    if value.get("format") != _REGISTRY_FORMAT or not isinstance(value.get("executors"), list):
        raise ExecutorRegistryError("executor registry format is unsupported")
    entries: list[ExecutorRegistryEntry] = []
    seen: set[str] = set()
    for item in value["executors"]:
        if not isinstance(item, dict) or set(item) != _ENTRY_FIELDS:
            raise ExecutorRegistryError("executor entry has unknown or missing fields")
        identifier = item.get("id")
        adapter = item.get("adapter")
        protocol = item.get("protocol")
        network_policy = item.get("network_policy")
        if not isinstance(identifier, str) or not _EXECUTOR_ID_PATTERN.fullmatch(identifier):
            raise ExecutorRegistryError("executor id is invalid")
        if identifier in seen:
            raise ExecutorRegistryError("executor ids must be unique")
        seen.add(identifier)
        if network_policy != "enforced":
            raise ExecutorRegistryError("executor must enforce the admitted network policy")
        if adapter == "builtin":
            if identifier != "codex" or protocol != "codex-app-server-v1":
                raise ExecutorRegistryError("only the reviewed Codex built-in may use the builtin adapter")
            runtime_root = executable = None
        elif adapter == "json-process":
            if protocol != "mission-executor-json-v1":
                raise ExecutorRegistryError("JSON process executor protocol is unsupported")
            if verify_runtimes:
                runtime_root, executable = _validated_runtime(
                    registry_path.parent,
                    identifier,
                    require_root_owned=require_root_owned,
                )
            else:
                runtime_root = registry_path.parent / identifier
                executable = runtime_root / "bin" / "mission-executor"
        else:
            raise ExecutorRegistryError("executor adapter is unsupported")
        entries.append(ExecutorRegistryEntry(identifier, adapter, protocol, runtime_root, executable))
    return ExecutorRegistry(_REGISTRY_FORMAT, tuple(entries))


def build_executor_factories(registry: ExecutorRegistry) -> dict[str, Callable[[], object | None]]:
    from .executor import JsonProcessMissionAgentExecutor

    factories: dict[str, Callable[[], object | None]] = {}
    for entry in registry.entries:
        if entry.adapter == "builtin":
            factories[entry.id] = lambda: None
        else:
            def factory(selected: ExecutorRegistryEntry = entry):
                return JsonProcessMissionAgentExecutor(
                    selected.id,
                    runtime_root=selected.runtime_root,
                    executable=selected.executable,
                )
            factories[entry.id] = factory
    return factories


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a Missions executor registry")
    parser.add_argument("registry")
    parser.add_argument("--verify-runtimes", action="store_true")
    parser.add_argument("--require-root-owned", action="store_true")
    parser.add_argument("--format", choices=("human", "json"), default="human")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        registry = load_executor_registry(
            args.registry,
            verify_runtimes=args.verify_runtimes,
            require_root_owned=args.require_root_owned,
        )
    except ExecutorRegistryError as exc:
        if args.format == "json":
            print(json.dumps({"format": _REGISTRY_FORMAT, "valid": False, "error": str(exc)}, sort_keys=True))
        else:
            print(f"Executor registry is invalid: {exc}")
        return 2
    identifiers = [entry.id for entry in registry.entries]
    if args.format == "json":
        print(json.dumps({"format": registry.format, "valid": True, "executors": identifiers}, sort_keys=True))
    else:
        print(f"Executor registry is valid: {', '.join(identifiers) or 'no executors'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
