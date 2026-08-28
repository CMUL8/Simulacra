"""Postgres-backed CRUD for identity + tenants (used when DATABASE_URL is set)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from .db import connection


def _iso(dt: Any) -> str:
	if dt is None:
		return datetime.now(UTC).isoformat()
	if isinstance(dt, datetime):
		if dt.tzinfo is None:
			dt = dt.replace(tzinfo=UTC)
		return dt.isoformat()
	return str(dt)


# ── Tenants ──────────────────────────────────────────────────────────


def pg_list_tenants() -> list[dict[str, Any]]:
	with connection() as conn:
		rows = conn.execute("SELECT * FROM tenants ORDER BY created_at").fetchall()
	out = []
	for r in rows:
		pol = r["policy"]
		if isinstance(pol, str):
			pol = json.loads(pol)
		out.append(
			{
				"id": r["id"],
				"name": r["name"],
				"status": r["status"],
				"notes": r["notes"] or "",
				"policy": pol or {},
				"created_at": _iso(r["created_at"]),
			}
		)
	return out


def pg_upsert_tenant(data: dict[str, Any]) -> None:
	with connection() as conn:
		conn.execute(
			"""
			INSERT INTO tenants (id, name, status, notes, policy, created_at)
			VALUES (%(id)s, %(name)s, %(status)s, %(notes)s, %(policy)s::jsonb, %(created_at)s)
			ON CONFLICT (id) DO UPDATE SET
			  name = EXCLUDED.name,
			  status = EXCLUDED.status,
			  notes = EXCLUDED.notes,
			  policy = EXCLUDED.policy
			""",
			{
				"id": data["id"],
				"name": data["name"],
				"status": data.get("status", "active"),
				"notes": data.get("notes", ""),
				"policy": json.dumps(data.get("policy") or {}),
				"created_at": data.get("created_at") or datetime.now(UTC).isoformat(),
			},
		)


def pg_update_tenant(tenant_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
	rows = pg_list_tenants()
	found = next((t for t in rows if t["id"] == tenant_id), None)
	if not found:
		return None
	found.update({k: v for k, v in patch.items() if v is not None})
	if "policy" in patch and patch["policy"] is not None:
		found["policy"] = {**(found.get("policy") or {}), **patch["policy"]}
	pg_upsert_tenant(found)
	return found


# ── Users ────────────────────────────────────────────────────────────


def pg_list_users() -> list[dict[str, Any]]:
	with connection() as conn:
		rows = conn.execute("SELECT * FROM users ORDER BY created_at").fetchall()
	return [
		{
			"id": r["id"],
			"email": r["email"],
			"name": r["name"],
			"password_hash": r["password_hash"],
			"is_platform_admin": bool(r["is_platform_admin"]),
			"status": r["status"],
			"created_at": _iso(r["created_at"]),
			"verified_email": r.get("verified_email"),
			"verified_email_at": _iso(r.get("verified_email_at")) if r.get("verified_email_at") else None,
			"provider_subject": r.get("provider_subject"),
		}
		for r in rows
	]


def pg_get_user(user_id: str) -> dict[str, Any] | None:
	with connection() as conn:
		r = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,)).fetchone()
	if not r:
		return None
	return {
		"id": r["id"],
		"email": r["email"],
		"name": r["name"],
		"password_hash": r["password_hash"],
		"is_platform_admin": bool(r["is_platform_admin"]),
		"status": r["status"],
		"created_at": _iso(r["created_at"]),
		"verified_email": r.get("verified_email"),
		"verified_email_at": _iso(r.get("verified_email_at")) if r.get("verified_email_at") else None,
		"provider_subject": r.get("provider_subject"),
	}


def pg_get_user_by_email(email: str) -> dict[str, Any] | None:
	with connection() as conn:
		r = conn.execute(
			"SELECT * FROM users WHERE lower(email) = lower(%s)", (email,)
		).fetchone()
	if not r:
		return None
	return pg_get_user(r["id"])


def pg_insert_user(data: dict[str, Any]) -> None:
	with connection() as conn:
		conn.execute(
			"""
			INSERT INTO users (id, email, name, password_hash, is_platform_admin, status, created_at, verified_email, verified_email_at, provider_subject)
			VALUES (%(id)s, %(email)s, %(name)s, %(password_hash)s, %(is_platform_admin)s, %(status)s, %(created_at)s, %(verified_email)s, %(verified_email_at)s, %(provider_subject)s)
			""",
			{
				"id": data["id"],
				"email": data["email"],
				"name": data.get("name", ""),
				"password_hash": data["password_hash"],
				"is_platform_admin": bool(data.get("is_platform_admin", False)),
				"status": data.get("status", "active"),
				"created_at": data.get("created_at") or datetime.now(UTC).isoformat(),
				"verified_email": data.get("verified_email"), "verified_email_at": data.get("verified_email_at"), "provider_subject": data.get("provider_subject"),
			},
		)


def pg_update_user_password(user_id: str, password_hash: str) -> None:
	with connection() as conn:
		conn.execute(
			"UPDATE users SET password_hash = %s WHERE id = %s",
			(password_hash, user_id),
		)


def pg_record_verified_provider_identity(user_id: str, provider_subject: str, verified_email: str) -> dict[str, Any]:
	with connection() as conn:
		row = conn.execute("SELECT provider_subject FROM users WHERE id = %s FOR UPDATE", (user_id,)).fetchone()
		if not row or row.get("provider_subject") not in {None, provider_subject}:
			raise PermissionError("verified identity subject mismatch")
		conn.execute("UPDATE users SET provider_subject = %s, verified_email = %s, verified_email_at = NOW() WHERE id = %s", (provider_subject, verified_email, user_id))
	return pg_get_user(user_id)  # type: ignore[return-value]


# ── Memberships ──────────────────────────────────────────────────────


def pg_list_memberships(
	*, tenant_id: str | None = None, user_id: str | None = None
) -> list[dict[str, Any]]:
	q = "SELECT * FROM memberships WHERE 1=1"
	params: list[Any] = []
	if tenant_id:
		q += " AND tenant_id = %s"
		params.append(tenant_id)
	if user_id:
		q += " AND user_id = %s"
		params.append(user_id)
	with connection() as conn:
		rows = conn.execute(q, params).fetchall()
	return [
		{
			"tenant_id": r["tenant_id"],
			"user_id": r["user_id"],
			"role": r["role"],
			"created_at": _iso(r["created_at"]),
			"transaction_id": r.get("transaction_id"),
			"visibility_state": r.get("visibility_state") or "committed",
		}
		for r in rows
	]


def pg_upsert_membership(data: dict[str, Any]) -> None:
	with connection() as conn:
		conn.execute(
			"""
			INSERT INTO memberships (tenant_id, user_id, role, created_at, transaction_id, visibility_state)
			VALUES (%(tenant_id)s, %(user_id)s, %(role)s, %(created_at)s, %(transaction_id)s, %(visibility_state)s)
			ON CONFLICT (tenant_id, user_id) DO UPDATE SET role = EXCLUDED.role, transaction_id = EXCLUDED.transaction_id, visibility_state = EXCLUDED.visibility_state
			""",
			{
				"tenant_id": data["tenant_id"],
				"user_id": data["user_id"],
				"role": data.get("role", "member"),
				"created_at": data.get("created_at") or datetime.now(UTC).isoformat(),
				"transaction_id": data.get("transaction_id"), "visibility_state": data.get("visibility_state", "committed"),
			},
		)


def pg_delete_membership(tenant_id: str, user_id: str) -> None:
	with connection() as conn:
		conn.execute(
			"DELETE FROM memberships WHERE tenant_id = %s AND user_id = %s",
			(tenant_id, user_id),
		)


def pg_update_membership_visibility(tenant_id: str, user_id: str, transaction_id: str, visibility_state: str) -> None:
	with connection() as conn:
		conn.execute("UPDATE memberships SET visibility_state = %s WHERE tenant_id = %s AND user_id = %s AND transaction_id = %s", (visibility_state, tenant_id, user_id, transaction_id))


# ── Sessions ─────────────────────────────────────────────────────────


def pg_insert_session(data: dict[str, Any]) -> None:
	with connection() as conn:
		conn.execute(
			"""
			INSERT INTO sessions (token_hash, user_id, expires_at, created_at)
			VALUES (%(token_hash)s, %(user_id)s, %(expires_at)s, %(created_at)s)
			""",
			data,
		)
		conn.execute("DELETE FROM sessions WHERE expires_at < NOW()")


def pg_find_session(token_hash: str) -> dict[str, Any] | None:
	with connection() as conn:
		r = conn.execute(
			"SELECT * FROM sessions WHERE token_hash = %s AND expires_at > NOW()",
			(token_hash,),
		).fetchone()
	if not r:
		return None
	return {
		"token_hash": r["token_hash"],
		"user_id": r["user_id"],
		"expires_at": _iso(r["expires_at"]),
		"created_at": _iso(r["created_at"]),
	}


def pg_delete_sessions_for_user(user_id: str) -> None:
	with connection() as conn:
		conn.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))


def pg_insert_reset_token(data: dict[str, Any]) -> None:
	with connection() as conn:
		conn.execute(
			"""
			INSERT INTO password_reset_tokens (token_hash, user_id, expires_at, used_at, created_at)
			VALUES (%(token_hash)s, %(user_id)s, %(expires_at)s, %(used_at)s, %(created_at)s)
			""",
			{
				"token_hash": data["token_hash"],
				"user_id": data["user_id"],
				"expires_at": data["expires_at"],
				"used_at": data.get("used_at"),
				"created_at": data.get("created_at") or datetime.now(UTC).isoformat(),
			},
		)
		conn.execute("DELETE FROM password_reset_tokens WHERE expires_at < NOW() AND used_at IS NOT NULL")


def pg_find_reset_token(token_hash: str) -> dict[str, Any] | None:
	with connection() as conn:
		r = conn.execute(
			"SELECT * FROM password_reset_tokens WHERE token_hash = %s",
			(token_hash,),
		).fetchone()
	if not r:
		return None
	return {
		"token_hash": r["token_hash"],
		"user_id": r["user_id"],
		"expires_at": _iso(r["expires_at"]),
		"used_at": _iso(r["used_at"]) if r.get("used_at") else None,
		"created_at": _iso(r["created_at"]),
	}


def pg_mark_reset_used(token_hash: str) -> None:
	with connection() as conn:
		conn.execute(
			"UPDATE password_reset_tokens SET used_at = %s WHERE token_hash = %s",
			(datetime.now(UTC).isoformat(), token_hash),
		)


# ── API keys ─────────────────────────────────────────────────────────


def pg_insert_api_key(data: dict[str, Any]) -> None:
	with connection() as conn:
		conn.execute(
			"""
			INSERT INTO api_keys
			  (id, user_id, tenant_id, name, prefix, hash, created_at, last_used_at, revoked)
			VALUES
			  (%(id)s, %(user_id)s, %(tenant_id)s, %(name)s, %(prefix)s, %(hash)s,
			   %(created_at)s, %(last_used_at)s, %(revoked)s)
			""",
			{
				**data,
				"last_used_at": data.get("last_used_at"),
				"revoked": bool(data.get("revoked", False)),
			},
		)


def pg_list_api_keys(user_id: str) -> list[dict[str, Any]]:
	with connection() as conn:
		rows = conn.execute(
			"SELECT * FROM api_keys WHERE user_id = %s ORDER BY created_at DESC",
			(user_id,),
		).fetchall()
	return [
		{
			"id": r["id"],
			"user_id": r["user_id"],
			"tenant_id": r["tenant_id"],
			"name": r["name"],
			"prefix": r["prefix"],
			"created_at": _iso(r["created_at"]),
			"last_used_at": _iso(r["last_used_at"]) if r["last_used_at"] else None,
			"revoked": bool(r["revoked"]),
		}
		for r in rows
	]


def pg_find_api_key_by_hash(token_hash: str) -> dict[str, Any] | None:
	with connection() as conn:
		r = conn.execute(
			"SELECT * FROM api_keys WHERE hash = %s AND revoked = FALSE",
			(token_hash,),
		).fetchone()
	if not r:
		return None
	return {
		"id": r["id"],
		"user_id": r["user_id"],
		"tenant_id": r["tenant_id"],
		"name": r["name"],
		"prefix": r["prefix"],
		"hash": r["hash"],
		"created_at": _iso(r["created_at"]),
		"last_used_at": _iso(r["last_used_at"]) if r["last_used_at"] else None,
		"revoked": bool(r["revoked"]),
	}


def pg_touch_api_key(key_id: str) -> None:
	with connection() as conn:
		conn.execute(
			"UPDATE api_keys SET last_used_at = NOW() WHERE id = %s",
			(key_id,),
		)


def pg_revoke_api_key(key_id: str, user_id: str | None = None) -> None:
	with connection() as conn:
		if user_id:
			conn.execute(
				"UPDATE api_keys SET revoked = TRUE WHERE id = %s AND user_id = %s",
				(key_id, user_id),
			)
		else:
			conn.execute("UPDATE api_keys SET revoked = TRUE WHERE id = %s", (key_id,))


# ── Audit ────────────────────────────────────────────────────────────


def pg_insert_audit(evt: dict[str, Any]) -> None:
	with connection() as conn:
		conn.execute(
			"""
			INSERT INTO audit_events (id, ts, action, tenant_id, user_id, resource, status, detail)
			VALUES (%(id)s, %(ts)s, %(action)s, %(tenant_id)s, %(user_id)s, %(resource)s, %(status)s, %(detail)s::jsonb)
			""",
			{
				"id": evt["id"],
				"ts": evt["ts"],
				"action": evt["action"],
				"tenant_id": evt.get("tenant_id"),
				"user_id": evt.get("user_id"),
				"resource": evt.get("resource"),
				"status": evt.get("status", "ok"),
				"detail": json.dumps(evt.get("detail") or {}),
			},
		)


def pg_list_audit(*, tenant_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
	limit = max(1, min(int(limit), 5000))
	with connection() as conn:
		if tenant_id and tenant_id != "*":
			rows = conn.execute(
				"""
				SELECT * FROM audit_events
				WHERE tenant_id = %s
				ORDER BY ts DESC LIMIT %s
				""",
				(tenant_id, limit),
			).fetchall()
		else:
			rows = conn.execute(
				"SELECT * FROM audit_events ORDER BY ts DESC LIMIT %s",
				(limit,),
			).fetchall()
	out = []
	for r in rows:
		detail = r["detail"]
		if isinstance(detail, str):
			detail = json.loads(detail)
		out.append(
			{
				"id": r["id"],
				"ts": _iso(r["ts"]),
				"action": r["action"],
				"tenant_id": r["tenant_id"],
				"user_id": r["user_id"],
				"resource": r["resource"],
				"status": r["status"],
				"detail": detail or {},
			}
		)
	return out
