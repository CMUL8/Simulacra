from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Mapping

from .errors import GraphParseError


def canonical_json_bytes(value: Any) -> bytes:
	return (
		json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
	).encode("utf-8")


def deterministic_json(value: Any, *, indent: int | None = None) -> str:
	separators = (",", ":") if indent is None else None
	return (
		json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True, indent=indent, separators=separators)
		+ "\n"
	)


def parse_operation_graph(data: str | bytes, *, syntax: str = "json") -> dict[str, Any]:
	if isinstance(data, bytes):
		try:
			data = data.decode("utf-8")
		except UnicodeDecodeError as exc:
			raise GraphParseError(f"Operation Graph is not UTF-8: {exc}") from exc
	try:
		if syntax.lower() == "json":
			value = json.loads(data)
		elif syntax.lower() in {"yaml", "yml"}:
			try:
				import yaml
			except ImportError as exc:
				raise GraphParseError("YAML support requires the already-optional PyYAML package") from exc
			value = yaml.safe_load(data)
		else:
			raise GraphParseError(f"Unsupported Operation Graph syntax: {syntax!r}")
	except GraphParseError:
		raise
	except Exception as exc:
		raise GraphParseError(f"Invalid {syntax.upper()} Operation Graph: {exc}") from exc
	if not isinstance(value, Mapping):
		raise GraphParseError("Operation Graph root must be an object")
	return copy.deepcopy(dict(value))


def load_operation_graph(path: str | Path) -> dict[str, Any]:
	path = Path(path)
	suffix = path.suffix.lower()
	if suffix not in {".json", ".yaml", ".yml"}:
		raise GraphParseError(f"Cannot infer syntax from {path.name!r}; use .json, .yaml, or .yml")
	try:
		data = path.read_bytes()
	except OSError as exc:
		raise GraphParseError(f"Cannot read Operation Graph {path}: {exc}") from exc
	return parse_operation_graph(data, syntax="json" if suffix == ".json" else "yaml")
