"""No-follow, atomically replaced files confined to an Operation Graph project."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


def _open_directory(path: str | Path, *, dir_fd: int | None = None) -> int:
	no_follow = getattr(os, "O_NOFOLLOW", None)
	if no_follow is None:
		raise PermissionError("safe Operation Graph publication requires no-follow filesystem support")
	flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | no_follow
	try:
		fd = os.open(path if dir_fd is None else Path(path).name, flags, dir_fd=dir_fd)
		if not stat.S_ISDIR(os.fstat(fd).st_mode):
			os.close(fd)
			raise PermissionError("Operation Graph publication path is not a directory")
		return fd
	except OSError as exc:
		raise PermissionError("Operation Graph publication path may not be a symlink") from exc


def atomic_write_project_file(project_root: Path, relative_path: Path, content: bytes) -> Path:
	"""Publish bytes under *project_root* without following child symlinks.

	Directory descriptors pin every parent.  A pre-existing final-file symlink
	is replaced as a directory entry, never opened; a symlinked parent fails
	before any external target is touched.
	"""
	root = Path(project_root)
	if relative_path.is_absolute() or not relative_path.parts or any(part in {"", ".", ".."} for part in relative_path.parts):
		raise ValueError("Operation Graph publication path must be a relative child path")
	filename = relative_path.name
	if filename in {"", ".", ".."}:
		raise ValueError("Operation Graph publication requires a file name")
	root_fd = _open_directory(root)
	parent_fd = root_fd
	temporary: str | None = None
	try:
		for component in relative_path.parts[:-1]:
			try:
				os.mkdir(component, mode=0o755, dir_fd=parent_fd)
			except FileExistsError:
				pass
			next_fd = _open_directory(Path(component), dir_fd=parent_fd)
			if parent_fd != root_fd:
				os.close(parent_fd)
			parent_fd = next_fd
		for _ in range(16):
			candidate = f".{filename}.{secrets.token_hex(16)}.tmp"
			try:
				fd = os.open(
					candidate,
					os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
					0o600,
					dir_fd=parent_fd,
				)
				temporary = candidate
				break
			except FileExistsError:
				continue
		else:
			raise PermissionError("could not safely allocate Operation Graph publication file")
		try:
			with os.fdopen(fd, "wb") as handle:
				handle.write(content)
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(temporary, filename, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
			temporary = None
			os.fsync(parent_fd)
		except OSError as exc:
			raise PermissionError("could not safely publish Operation Graph artifact") from exc
	finally:
		if temporary is not None:
			try:
				os.unlink(temporary, dir_fd=parent_fd)
			except OSError:
				pass
		if parent_fd != root_fd:
			os.close(parent_fd)
		os.close(root_fd)
	return root / relative_path
