"""Postgres identity helpers (no live DB required)."""

from __future__ import annotations

from simulacra.demo.db import database_url, health, migrate, using_postgres


def test_json_backend_when_no_url(monkeypatch):
	monkeypatch.delenv("SIMULACRA_DATABASE_URL", raising=False)
	monkeypatch.delenv("DATABASE_URL", raising=False)
	assert database_url() is None
	assert using_postgres() is False
	assert migrate() == {"backend": "json", "migrated": False}
	assert health()["backend"] == "json"
	assert health()["ok"] is True


def test_database_url_prefers_simulacra(monkeypatch):
	monkeypatch.setenv("DATABASE_URL", "postgresql://a")
	monkeypatch.setenv("SIMULACRA_DATABASE_URL", "postgresql://b")
	assert database_url() == "postgresql://b"
	assert using_postgres() is True


def test_user_verified_email_columns_are_expand_only():
	from simulacra.demo.db import SCHEMA_SQL
	assert "verified_email TEXT" in SCHEMA_SQL
	assert "verified_email_at TIMESTAMPTZ" in SCHEMA_SQL
	assert "provider_subject TEXT" in SCHEMA_SQL
	assert "transaction_id TEXT" in SCHEMA_SQL
	assert "visibility_state TEXT NOT NULL DEFAULT 'committed'" in SCHEMA_SQL


def test_legacy_membership_defaults_to_committed_visibility(tmp_path, monkeypatch):
	from simulacra.demo import identity
	data = tmp_path / "data"; data.mkdir()
	monkeypatch.setattr(identity, "DATA_DIR", data)
	monkeypatch.setattr(identity, "MEMBERSHIPS_PATH", data / "memberships.json")
	identity.MEMBERSHIPS_PATH.write_text('{"memberships":[{"tenant_id":"tenant_1","user_id":"user_1","role":"member","created_at":"2026-01-01T00:00:00+00:00"}]}')
	memberships = identity.list_memberships(tenant_id="tenant_1", user_id="user_1")
	assert len(memberships) == 1
	assert memberships[0].transaction_id is None and memberships[0].visibility_state == "committed"
