"""Platform / tenant audit trail for enterprise compliance + SIEM export."""

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

	from .db import using_postgres

	if using_postgres():
		try:
			from .pg_store import pg_insert_audit

			pg_insert_audit(evt)
		except Exception:  # noqa: BLE001 — never break the request path
			_write_jsonl(evt)
	else:
		_write_jsonl(evt)

	# Fire-and-forget SIEM forward
	try:
		from .siem import forward_event_async, siem_webhook

		if siem_webhook():
			forward_event_async(evt)
	except Exception:  # noqa: BLE001
		pass
	return evt


def _write_jsonl(evt: dict[str, Any]) -> None:
	AUDIT_DIR.mkdir(parents=True, exist_ok=True)
	with (AUDIT_DIR / "platform.jsonl").open("a", encoding="utf-8") as f:
		f.write(json.dumps(evt, default=str) + "\n")
	tenant_id = evt.get("tenant_id")
	if tenant_id and tenant_id != "*":
		path = AUDIT_DIR / f"tenant_{tenant_id}.jsonl"
		with path.open("a", encoding="utf-8") as f:
			f.write(json.dumps(evt, default=str) + "\n")


def list_audit(
	*,
	tenant_id: str | None = None,
	limit: int = 100,
) -> list[dict[str, Any]]:
	from .db import using_postgres

	if using_postgres():
		try:
			from .pg_store import pg_list_audit

			return pg_list_audit(tenant_id=tenant_id, limit=limit)
		except Exception:  # noqa: BLE001
			pass
	path = AUDIT_DIR / (
		f"tenant_{tenant_id}.jsonl" if tenant_id and tenant_id != "*" else "platform.jsonl"
	)
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
