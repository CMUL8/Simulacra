"""FastAPI auth dependencies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, Request

from simulacra.demo.enterprise_audit import emit_audit
from simulacra.demo.identity import AuthContext, ensure_bootstrap, resolve_auth
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
