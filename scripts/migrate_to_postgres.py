#!/usr/bin/env python3
"""Migrate local JSON identity/tenants/audit into Postgres.

Usage:
  SIMULACRA_DATABASE_URL=postgresql://... python scripts/migrate_to_postgres.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from simulacra.demo.db import migrate, using_postgres  # noqa: E402
from simulacra.demo.paths import REPO_ROOT  # noqa: E402
from simulacra.demo.pg_store import (  # noqa: E402
	pg_insert_api_key,
	pg_insert_audit,
	pg_insert_user,
	pg_upsert_membership,
	pg_upsert_tenant,
)


def _load(path: Path, default):
	if not path.exists():
		return default
	return json.loads(path.read_text())


def main() -> int:
	if not using_postgres():
		print("Set SIMULACRA_DATABASE_URL first", file=sys.stderr)
		return 1
	print(migrate())
	data = REPO_ROOT / "data"
	tenants_path = REPO_ROOT / "tenants" / "tenants.json"
	tenants = _load(tenants_path, {"tenants": []}).get("tenants", [])
	for t in tenants:
		pg_upsert_tenant(t)
		print(f"tenant {t['id']}")
	for u in _load(data / "users.json", {"users": []}).get("users", []):
		pg_insert_user(u)
		print(f"user {u['email']}")
	for m in _load(data / "memberships.json", {"memberships": []}).get("memberships", []):
		pg_upsert_membership(m)
	for k in _load(data / "api_keys.json", {"keys": []}).get("keys", []):
		pg_insert_api_key(k)
	audit = data / "audit" / "platform.jsonl"
	if audit.exists():
		n = 0
		for line in audit.read_text().splitlines():
			if not line.strip():
				continue
			pg_insert_audit(json.loads(line))
			n += 1
		print(f"audit events {n}")
	print("done")
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
