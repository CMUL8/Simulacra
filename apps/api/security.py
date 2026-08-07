"""FastAPI auth dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request

from simulacra.demo.enterprise_audit import emit_audit
from simulacra.demo.identity import AuthContext, ensure_bootstrap, resolve_auth
from simulacra.demo.runs import load_state


def get_auth(
	authorization: Annotated[str | None, Header()] = None,
	x_tenant_id: Annotated[str | None, Header()] = None,
	token: Annotated[str | None, Query()] = None,
) -> AuthContext:
	ensure_bootstrap()
	# Allow ?token= for EventSource (cannot set Authorization header)
	auth = authorization
	if (not auth or not auth.lower().startswith("bearer ")) and token:
		auth = f"Bearer {token}"
	try:
		return resolve_auth(auth, tenant_header=x_tenant_id)
	except PermissionError as exc:
		raise HTTPException(
			401 if "required" in str(exc).lower() or "invalid" in str(exc).lower() else 403,
			str(exc),
		) from exc
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc


def require_perm(permission: str):
	def _dep(ctx: Annotated[AuthContext, Depends(get_auth)]) -> AuthContext:
		try:
			ctx.require(permission)
		except PermissionError as exc:
			raise HTTPException(403, str(exc)) from exc
		return ctx

	return _dep


def require_project_access(permission: str = "project:read"):
	def _dep(
		project_id: str,
		ctx: Annotated[AuthContext, Depends(require_perm(permission))],
	) -> AuthContext:
		try:
			state = load_state(project_id)
		except FileNotFoundError as exc:
			raise HTTPException(404, "Project not found") from exc
		if ctx.tenant_id != "*" and state.tenant_id != ctx.tenant_id and not ctx.user.is_platform_admin:
			raise HTTPException(404, "Project not found")  # don't leak existence
		return ctx

	return _dep


def audit_request(request: Request, ctx: AuthContext, action: str, **detail: object) -> None:
	emit_audit(
		action=action,
		tenant_id=ctx.tenant_id,
		user_id=ctx.user.id,
		resource=str(request.url.path),
		detail={k: v for k, v in detail.items()},
	)
