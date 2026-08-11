"""Auth + tenancy tests (no network)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Isolate identity store under tmp
@pytest.fixture(autouse=True)
def _isolated_data(tmp_path, monkeypatch):
	data = tmp_path / "data"
	tenants = tmp_path / "tenants"
	runs = tmp_path / "runs"
	data.mkdir()
	tenants.mkdir()
	runs.mkdir()
	monkeypatch.setenv("SIMULACRA_AUTH_REQUIRED", "1")
	monkeypatch.setenv("SIMULACRA_BOOTSTRAP_EMAIL", "admin@test.local")
	monkeypatch.setenv("SIMULACRA_BOOTSTRAP_PASSWORD", "test-password-123")
	monkeypatch.setenv("SIMULACRA_DEFAULT_TENANT", "default")
	monkeypatch.delenv("SIMULACRA_DATABASE_URL", raising=False)
	monkeypatch.delenv("DATABASE_URL", raising=False)
	import simulacra.demo.identity as identity
	import simulacra.demo.tenants as tenants_mod
	import simulacra.demo.paths as paths
	import simulacra.demo.enterprise_audit as audit

	monkeypatch.setattr(identity, "DATA_DIR", data)
	monkeypatch.setattr(identity, "USERS_PATH", data / "users.json")
	monkeypatch.setattr(identity, "KEYS_PATH", data / "api_keys.json")
	monkeypatch.setattr(identity, "SESSIONS_PATH", data / "sessions.json")
	monkeypatch.setattr(identity, "MEMBERSHIPS_PATH", data / "memberships.json")
	monkeypatch.setattr(tenants_mod, "TENANTS_PATH", tenants / "tenants.json")
	monkeypatch.setattr(paths, "RUNS_DIR", runs)
	monkeypatch.setattr(audit, "AUDIT_DIR", data / "audit")
	yield


def test_bootstrap_and_login():
	from simulacra.demo.identity import ensure_bootstrap, login_user, resolve_auth

	ensure_bootstrap()
	user, token = login_user("admin@test.local", "test-password-123")
	assert user.is_platform_admin
	ctx = resolve_auth(f"Bearer {token}", tenant_header="default")
	assert ctx.role == "owner"
	ctx.require("project:write")


def test_register_creates_tenant():
	from simulacra.demo.identity import ensure_bootstrap, register_user, user_tenants

	ensure_bootstrap()
	user, token = register_user("alice@acme.com", "password12345", name="Alice", tenant_name="Acme")
	tenants = user_tenants(user)
	assert any(t["name"] == "Acme" for t in tenants)
	assert token.startswith("sst_")


def test_stale_default_tenant_header_recovers():
	"""Console used to send X-Tenant-Id: default and kick non-default members out."""
	from simulacra.demo.identity import ensure_bootstrap, register_user, resolve_auth

	ensure_bootstrap()
	user, token = register_user("bob@acme.com", "password12345", name="Bob", tenant_name="BobCo")
	# Wrong/stale header must not 403 — recover to Bob's workspace
	ctx = resolve_auth(f"Bearer {token}", tenant_header="default")
	assert ctx.user.id == user.id
	assert ctx.tenant_id != "default"
	ctx_none = resolve_auth(f"Bearer {token}", tenant_header=None)
	assert ctx_none.tenant_id == ctx.tenant_id


def test_rbac_viewer_cannot_write():
	from simulacra.demo.identity import (
		AuthContext,
		add_membership,
		create_user,
		ensure_bootstrap,
	)
	from simulacra.demo.tenants import create_tenant

	ensure_bootstrap()
	tenant = create_tenant("Locked")
	viewer = create_user("view@x.com", "password12345")
	add_membership(tenant.id, viewer.id, "viewer")
	ctx = AuthContext(user=viewer, tenant_id=tenant.id, role="viewer", auth_via="session")
	with pytest.raises(PermissionError):
		ctx.require("project:write")


def test_project_quota():
	from simulacra.demo.identity import ensure_bootstrap
	from simulacra.demo.runs import create_project
	from simulacra.demo.tenants import create_tenant, update_tenant

	ensure_bootstrap()
	tenant = create_tenant("Tiny", policy={"max_projects": 1})
	update_tenant(tenant.id, policy={"max_projects": 1})
	create_project("Build app number one here", tenant_id=tenant.id, use_fixture=False)
	with pytest.raises(PermissionError):
		create_project("Build app number two here", tenant_id=tenant.id, use_fixture=False)
