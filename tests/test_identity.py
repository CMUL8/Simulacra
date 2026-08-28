"""Auth + tenancy tests (no network)."""

from __future__ import annotations

import os
import sys
import types
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
	monkeypatch.setattr(identity, "RESET_TOKENS_PATH", data / "password_reset_tokens.json")
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


def test_register_derives_personal_mission_name_without_workspace():
	from simulacra.demo.identity import register_user, user_tenants

	user, _ = register_user("first.last@example.com", "password12345", name="First Last")
	assert any(t["name"] == "First Last's Mission" for t in user_tenants(user))


def test_clerk_avatar_claim_requires_bounded_http_url():
	from simulacra.demo.clerk_auth import _trusted_avatar_url

	assert _trusted_avatar_url({"image_url": "https://images.example/avatar.png"}) == "https://images.example/avatar.png"
	assert _trusted_avatar_url({"image_url": "file:///private/avatar.png"}) is None
	assert _trusted_avatar_url({"image_url": "https://images.example/" + "a" * 2048}) is None


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


def test_password_reset_flow():
	from simulacra.demo.identity import (
		ensure_bootstrap,
		login_user,
		register_user,
		request_password_reset,
		reset_password_with_token,
	)

	ensure_bootstrap()
	register_user("cara@acme.com", "oldpassword1", name="Cara", tenant_name="CaraCo")
	req = request_password_reset("cara@acme.com")
	assert req["ok"] is True
	assert req.get("token", "").startswith("spr_")
	reset_password_with_token(req["token"], "newpassword9")
	user, _ = login_user("cara@acme.com", "newpassword9")
	assert user.email == "cara@acme.com"
	# reused token fails
	import pytest

	with pytest.raises(PermissionError):
		reset_password_with_token(req["token"], "anotherpass1")
	# unknown email still ok (no leak)
	quiet = request_password_reset("nobody@missing.test")
	assert quiet["ok"] is True
	assert "token" not in quiet


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


def test_only_admins_and_owners_can_approve_projects():
	from simulacra.demo.identity import AuthContext, User

	user = User(id="usr_review", email="review@example.test", name="Reviewer", password_hash="unused")
	member = AuthContext(user=user, tenant_id="tenant_review", role="member", auth_via="test")
	with pytest.raises(PermissionError):
		member.require("project:approve")
	AuthContext(user=user, tenant_id="tenant_review", role="admin", auth_via="test").require("project:approve")


def _invitation_claims(subject="subject_1", email="invitee@example.test", *, verified=True, proof_subject=None):
	return {"sub": subject, "proof": {"verified": verified, "email": email, "subject": proof_subject}}


def _mock_invitation_decoder(monkeypatch, result):
	from simulacra.demo import clerk_auth
	def decode(*_args, **_kwargs):
		if isinstance(result, Exception): raise result
		return result
	monkeypatch.setitem(sys.modules, "jose", types.SimpleNamespace(jwt=types.SimpleNamespace(decode=decode)))
	monkeypatch.setattr(clerk_auth, "_fetch_jwks", lambda: {"keys": []})
	monkeypatch.setenv("CLERK_ISSUER", "https://issuer.example")
	monkeypatch.setenv("CLERK_AUDIENCE", "audience")
	monkeypatch.setenv("CLERK_INVITATION_EMAIL_CLAIM", "proof")


def test_clerk_session_verification_uses_exact_configured_issuer_and_audience(monkeypatch):
	from simulacra.demo import clerk_auth
	seen = {}
	def decode(*_args, **kwargs):
		seen.update(kwargs)
		return {"sub": "subject_1"}
	monkeypatch.setitem(sys.modules, "jose", types.SimpleNamespace(jwt=types.SimpleNamespace(decode=decode)))
	monkeypatch.setattr(clerk_auth, "_fetch_jwks", lambda: {"keys": []})
	monkeypatch.setenv("CLERK_ISSUER", "https://issuer.example")
	monkeypatch.setenv("CLERK_AUDIENCE", "missions-console")

	assert clerk_auth.verify_clerk_jwt("session-token")["sub"] == "subject_1"
	assert seen["issuer"] == "https://issuer.example"
	assert seen["audience"] == "missions-console"
	assert seen["options"] == {"verify_aud": True, "verify_iss": True, "verify_exp": True}


def test_clerk_session_verification_rejects_missing_config_in_production(monkeypatch):
	from simulacra.demo.clerk_auth import verify_clerk_jwt
	monkeypatch.setenv("SIMULACRA_ENVIRONMENT", "production")
	monkeypatch.delenv("CLERK_ISSUER", raising=False)
	monkeypatch.delenv("CLERK_AUDIENCE", raising=False)
	with pytest.raises(PermissionError):
		verify_clerk_jwt("session-token")


def test_clerk_invitation_principal_rejects_invalid_signature(monkeypatch):
	from simulacra.demo.clerk_auth import verified_invitation_email
	_mock_invitation_decoder(monkeypatch, ValueError("bad signature"))
	with pytest.raises(PermissionError): verified_invitation_email("bad")


def test_clerk_invitation_principal_rejects_expired_token(monkeypatch):
	from simulacra.demo.clerk_auth import verified_invitation_email
	_mock_invitation_decoder(monkeypatch, ValueError("expired"))
	with pytest.raises(PermissionError): verified_invitation_email("expired")


def test_clerk_invitation_principal_rejects_missing_subject(monkeypatch):
	from simulacra.demo.clerk_auth import verified_invitation_email
	_mock_invitation_decoder(monkeypatch, _invitation_claims(subject=""))
	with pytest.raises(PermissionError): verified_invitation_email("missing-subject")


def test_clerk_invitation_principal_rejects_missing_trusted_email_proof_config(monkeypatch):
	from simulacra.demo.clerk_auth import verified_invitation_email
	monkeypatch.delenv("CLERK_ISSUER", raising=False); monkeypatch.delenv("CLERK_AUDIENCE", raising=False)
	monkeypatch.delenv("CLERK_INVITATION_EMAIL_CLAIM", raising=False)
	with pytest.raises(PermissionError): verified_invitation_email("missing-config")


def test_clerk_provider_lookup_is_bound_to_verified_subject():
	from simulacra.demo.clerk_auth import ensure_verified_invitation_user
	from simulacra.demo.identity import create_user
	create_user("bound@example.test", "password12345")
	first = ensure_verified_invitation_user("provider_subject_a", "bound@example.test")
	assert first.provider_subject == "provider_subject_a"
	with pytest.raises(PermissionError): ensure_verified_invitation_user("provider_subject_b", "bound@example.test")


def test_clerk_invitation_principal_rejects_invalid_issuer_or_audience(monkeypatch):
	from simulacra.demo.clerk_auth import verified_invitation_email
	_mock_invitation_decoder(monkeypatch, ValueError("issuer/audience mismatch"))
	with pytest.raises(PermissionError): verified_invitation_email("wrong-issuer")


@pytest.mark.parametrize("claims", [_invitation_claims(verified=False), _invitation_claims(email="subject_1@users.example")])
def test_clerk_invitation_principal_rejects_unverified_or_synthetic_fallback_email(monkeypatch, claims):
	from simulacra.demo.clerk_auth import verified_invitation_email
	_mock_invitation_decoder(monkeypatch, claims)
	with pytest.raises(PermissionError): verified_invitation_email("unverified")


def test_clerk_invitation_principal_rejects_subject_mismatch(monkeypatch):
	from simulacra.demo.clerk_auth import verified_invitation_email
	_mock_invitation_decoder(monkeypatch, _invitation_claims(proof_subject="other_subject"))
	with pytest.raises(PermissionError): verified_invitation_email("subject-mismatch")


def test_local_invitation_email_verification_is_denied_in_production(monkeypatch):
	from simulacra.demo.clerk_auth import local_invitation_fixture_principal
	monkeypatch.setenv("SIMULACRA_ENABLE_LOCAL_INVITATION_FIXTURE", "1")
	monkeypatch.setenv("SIMULACRA_ENVIRONMENT", "production")
	assert local_invitation_fixture_principal("subject_1|invitee@example.test") is None


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
