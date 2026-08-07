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
