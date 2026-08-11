"""Postgres connection + schema for identity / tenancy / audit.

Enabled when SIMULACRA_DATABASE_URL or DATABASE_URL is set.
Falls back to JSON file stores when unset (local demo).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger("simulacra.db")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tenants (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  notes TEXT NOT NULL DEFAULT '',
  policy JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  is_platform_admin BOOLEAN NOT NULL DEFAULT FALSE,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS memberships (
  tenant_id TEXT NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  role TEXT NOT NULL DEFAULT 'member',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (tenant_id, user_id)
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS password_reset_tokens (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TIMESTAMPTZ NOT NULL,
  used_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tenant_id TEXT,
  name TEXT NOT NULL DEFAULT 'default',
  prefix TEXT NOT NULL,
  hash TEXT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_used_at TIMESTAMPTZ,
  revoked BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS audit_events (
  id TEXT PRIMARY KEY,
  ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  action TEXT NOT NULL,
  tenant_id TEXT,
  user_id TEXT,
  resource TEXT,
  status TEXT NOT NULL DEFAULT 'ok',
  detail JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_exp ON sessions(expires_at);
CREATE INDEX IF NOT EXISTS idx_reset_user ON password_reset_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_reset_exp ON password_reset_tokens(expires_at);
CREATE INDEX IF NOT EXISTS idx_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_memberships_user ON memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_ts ON audit_events(tenant_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_events(ts DESC);
"""


def database_url() -> str | None:
	url = (
		os.environ.get("SIMULACRA_DATABASE_URL")
		or os.environ.get("DATABASE_URL")
		or ""
	).strip()
	return url or None


def using_postgres() -> bool:
	return database_url() is not None


_pool = None


def _connect():
	url = database_url()
	if not url:
		raise RuntimeError("No DATABASE_URL configured")
	try:
		import psycopg
		from psycopg.rows import dict_row
	except ImportError as exc:
		raise RuntimeError(
			"psycopg is required for Postgres identity store — pip install 'psycopg[binary]'"
		) from exc
	conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
	return conn


@contextmanager
def connection() -> Iterator[Any]:
	conn = _connect()
	try:
		yield conn
		conn.commit()
	except Exception:
		conn.rollback()
		raise
	finally:
		conn.close()


def migrate() -> dict[str, Any]:
	"""Apply schema. No-op if Postgres not configured."""
	if not using_postgres():
		return {"backend": "json", "migrated": False}
	with connection() as conn:
		conn.execute(SCHEMA_SQL)
	log.info("postgres schema ready")
	return {"backend": "postgres", "migrated": True}


def health() -> dict[str, Any]:
	if not using_postgres():
		return {"backend": "json", "ok": True}
	try:
		with connection() as conn:
			conn.execute("SELECT 1")
		return {"backend": "postgres", "ok": True}
	except Exception as exc:  # noqa: BLE001
		return {"backend": "postgres", "ok": False, "error": str(exc)[:200]}
