"""FastAPI auth dependencies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request

from simulacra.demo.enterprise_audit import emit_audit
from simulacra.demo.identity import AuthContext, ensure_bootstrap, resolve_auth


class InvitationAcceptPrincipal:
	"""Credential-scoped enrollment identity, deliberately not an AuthContext."""
	def __init__(self, *, actor_id: str, verified_email: str, provider_subject: str) -> None:
		self.actor_id = actor_id
		self.verified_email = verified_email
		self.provider_subject = provider_subject


def require_invitation_accept_authenticated_email(
	authorization: Annotated[str | None, Header()] = None,
) -> InvitationAcceptPrincipal:
	"""Verify only enrollment proof; do not resolve or create tenant membership."""
	if not authorization or not authorization.lower().startswith("bearer "):
		raise HTTPException(404, {"code": "invitation_unavailable", "message": "This invitation is unavailable."})
	token = authorization.split(" ", 1)[1].strip()
	try:
		from simulacra.demo.clerk_auth import ensure_verified_invitation_user, local_invitation_fixture_principal, verified_invitation_email
		fixture = local_invitation_fixture_principal(token)
		subject, email = fixture if fixture is not None else verified_invitation_email(token)
		user = ensure_verified_invitation_user(subject, email)
		return InvitationAcceptPrincipal(actor_id=user.id, verified_email=email, provider_subject=subject)
	except Exception as exc:  # intentionally indistinguishable from every unavailable invitation
		raise HTTPException(404, {"code": "invitation_unavailable", "message": "This invitation is unavailable."}) from exc
from simulacra.demo.runs import load_state


def get_auth(
	authorization: Annotated[str | None, Header()] = None,
	x_tenant_id: Annotated[str | None, Header()] = None,
	token: Annotated[str | None, Query()] = None,
	tenant: Annotated[str | None, Query()] = None,
) -> AuthContext:
	ensure_bootstrap()
	# Allow ?token=&tenant= for EventSource / preview iframe (no Authorization header)
	auth = authorization
	if (not auth or not auth.lower().startswith("bearer ")) and token:
		auth = f"Bearer {token}"
	tid = x_tenant_id or tenant
	try:
		return resolve_auth(auth, tenant_header=tid)
	except PermissionError as exc:
		raise HTTPException(
			401 if "required" in str(exc).lower() or "invalid" in str(exc).lower() else 403,
			str(exc),
		) from exc
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc


def require_perm(permission: str) -> Callable[..., AuthContext]:
	"""Return a FastAPI dependency that requires ``permission``."""

	def _dep(auth: Annotated[AuthContext, Depends(get_auth)]) -> AuthContext:
		try:
			auth.require(permission)
		except PermissionError as exc:
			raise HTTPException(403, str(exc)) from exc
		return auth

	_dep.__name__ = f"require_perm_{permission.replace(':', '_')}"
	return _dep


def require_project_access(permission: str = "project:read") -> Callable[..., AuthContext]:
	"""Auth + tenant check for a project path param.

	Important: do not nest ``Depends(require_perm(...))`` here — with
	``from __future__ import annotations`` FastAPI/Pydantic can fail to resolve
	the nested Annotated type and leak ``ctx`` as a required query param.
	"""

	def _dep(
		project_id: str,
		authorization: Annotated[str | None, Header()] = None,
		x_tenant_id: Annotated[str | None, Header()] = None,
		token: Annotated[str | None, Query()] = None,
		tenant: Annotated[str | None, Query()] = None,
	) -> AuthContext:
		auth = get_auth(
			authorization=authorization,
			x_tenant_id=x_tenant_id,
			token=token,
			tenant=tenant,
		)
		try:
			auth.require(permission)
		except PermissionError as exc:
			raise HTTPException(403, str(exc)) from exc
		try:
			state = load_state(project_id)
		except FileNotFoundError as exc:
			raise HTTPException(404, "Project not found") from exc
		if auth.tenant_id != "*" and state.tenant_id != auth.tenant_id and not auth.user.is_platform_admin:
			raise HTTPException(404, "Project not found")  # don't leak existence
		return auth

	_dep.__name__ = f"require_project_{permission.replace(':', '_')}"
	return _dep


def audit_request(request: Request, ctx: AuthContext, action: str, **detail: object) -> None:
	emit_audit(
		action=action,
		tenant_id=ctx.tenant_id,
		user_id=ctx.user.id,
		resource=str(request.url.path),
		detail={k: v for k, v in detail.items()},
	)
