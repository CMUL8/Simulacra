"""Platform / tenant audit trail for enterprise compliance."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import REPO_ROOT

AUDIT_DIR = REPO_ROOT / "data" / "audit"


def emit_audit(
	*,
	action: str,
	tenant_id: str | None,
	user_id: str | None,
	resource: str | None = None,
	detail: dict[str, Any] | None = None,
	status: str = "ok",
) -> dict[str, Any]:
	AUDIT_DIR.mkdir(parents=True, exist_ok=True)
	evt = {
		"id": f"aud_{uuid.uuid4().hex[:12]}",
		"ts": datetime.now(UTC).isoformat(),
		"action": action,
		"tenant_id": tenant_id,
		"user_id": user_id,
		"resource": resource,
		"status": status,
		"detail": detail or {},
	}
	# Global stream
	with (AUDIT_DIR / "platform.jsonl").open("a", encoding="utf-8") as f:
		f.write(json.dumps(evt, default=str) + "\n")
	# Per-tenant stream
	if tenant_id and tenant_id != "*":
		path = AUDIT_DIR / f"tenant_{tenant_id}.jsonl"
		with path.open("a", encoding="utf-8") as f:
			f.write(json.dumps(evt, default=str) + "\n")
	return evt


def list_audit(
	*,
	tenant_id: str | None = None,
	limit: int = 100,
) -> list[dict[str, Any]]:
	path = AUDIT_DIR / (f"tenant_{tenant_id}.jsonl" if tenant_id and tenant_id != "*" else "platform.jsonl")
	if not path.exists():
		return []
	lines = path.read_text(encoding="utf-8").strip().splitlines()
	out: list[dict[str, Any]] = []
	for line in lines[-limit:]:
		try:
			out.append(json.loads(line))
		except json.JSONDecodeError:
			continue
	out.reverse()
	return out
