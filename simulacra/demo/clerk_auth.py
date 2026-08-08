"""Clerk JWT auth for Simulacra (CMUL8 platform Clerk).

Verifies session JWTs against clerk.platform.cmul8.com JWKS and maps
Clerk orgs → Simulacra tenants. Enabled when CLERK_PUBLISHABLE_KEY or
CLERK_SECRET_KEY is set.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

log = logging.getLogger("simulacra.clerk")

CLERK_FRONTEND_API = os.environ.get(
	"CLERK_FRONTEND_API",
	"https://clerk.platform.cmul8.com",
).rstrip("/")
CLERK_JWKS_URL = f"{CLERK_FRONTEND_API}/.well-known/jwks.json"

# Same CMUL8 org map as game-survey platform (extend via CLERK_ORG_TO_WORKSPACE)
_DEFAULT_ORG_MAP = {
	"org_3EaJPxit9T45WcJavXTJTSYQ8PG": "nzta",
	"org_3EaJQ0ta8kmwjk0mHfsn1bBCs51": "cmul8",
	"org_3EaO1mbkcH90FqD5N7HAXi5YrLC": "bajaj-alts",
	"org_3EaO1hEZAqQfNBYpFDiI62udw0W": "seven-star-games",
	"org_3HAcpn1VvGjehldqcWz3CYiIpT4": "butterfly-health",
}

# Map workspace slug → Simulacra tenant id (default tenant for cmul8 org)
_DEFAULT_TENANT_MAP = {
	"cmul8": "default",
	"nzta": "default",
}


def clerk_enabled() -> bool:
	return bool(
		os.environ.get("CLERK_PUBLISHABLE_KEY")
		or os.environ.get("CLERK_SECRET_KEY")
		or os.environ.get("VITE_CLERK_PUBLISHABLE_KEY")
	)


def _org_map() -> dict[str, str]:
	out = dict(_DEFAULT_ORG_MAP)
	raw = os.environ.get("CLERK_ORG_TO_WORKSPACE", "").strip()
	for pair in raw.split(","):
		pair = pair.strip()
		if not pair or ":" not in pair:
			continue
		oid, slug = pair.split(":", 1)
		out[oid.strip()] = slug.strip()
	return out


def _tenant_for_slug(slug: str) -> str:
	raw = os.environ.get("CLERK_SLUG_TO_TENANT", "").strip()
	mapping = dict(_DEFAULT_TENANT_MAP)
	for pair in raw.split(","):
		pair = pair.strip()
		if not pair or ":" not in pair:
			continue
		s, tid = pair.split(":", 1)
		mapping[s.strip()] = tid.strip()
	return mapping.get(slug, slug)


def _super_admins() -> set[str]:
	default = (
		"user_3EaJPoPeR06W6IZf11eJAFU7czn,"  # abhi.katte@gmail.com
		"user_3Ew5hEnoPbAJmaNegmeBksFH43r"  # basab@fastcode.ai
	)
	raw = os.environ.get("SUPER_ADMIN_USER_IDS", default).strip()
	return {x.strip() for x in raw.split(",") if x.strip()}


_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0.0}


def _fetch_jwks() -> dict[str, Any]:
	import urllib.request

	now = time.time()
	if _JWKS_CACHE["keys"] and now - _JWKS_CACHE["fetched_at"] < 600:
		return _JWKS_CACHE["keys"]
	with urllib.request.urlopen(CLERK_JWKS_URL, timeout=5) as resp:
		import json

		data = json.loads(resp.read().decode())
	_JWKS_CACHE["keys"] = data
	_JWKS_CACHE["fetched_at"] = now
	return data


def verify_clerk_jwt(token: str) -> dict[str, Any]:
	"""Verify Clerk session JWT; returns claims. Raises PermissionError on failure."""
	try:
		from jose import jwt as jose_jwt
	except ImportError as exc:
		raise RuntimeError("python-jose required for Clerk — pip install 'python-jose[cryptography]'") from exc
	try:
		jwks = _fetch_jwks()
		return jose_jwt.decode(
			token,
			jwks,
			algorithms=["RS256"],
			options={"verify_aud": False, "verify_iss": False},
		)
	except Exception as exc:  # noqa: BLE001
		raise PermissionError(f"Invalid Clerk token: {exc}") from exc


def _clean_slug(slug: str) -> str:
	return re.sub(r"-\d{10,}$", "", slug)


def extract_org_id(claims: dict[str, Any]) -> str | None:
	if claims.get("org_id"):
		return str(claims["org_id"])
	o = claims.get("o") or {}
	if isinstance(o, dict) and o.get("id"):
		return str(o["id"])
	return None


def extract_org_slug(claims: dict[str, Any]) -> str | None:
	if claims.get("org_slug"):
		return _clean_slug(str(claims["org_slug"]))
	o = claims.get("o") or {}
	if isinstance(o, dict):
		for k in ("slg", "slug"):
			if o.get(k):
				return _clean_slug(str(o[k]))
	return None


def resolve_tenant_id(claims: dict[str, Any], tenant_header: str | None) -> str:
	"""Resolve Simulacra tenant from Clerk org + optional X-Tenant-Id."""
	from .tenants import create_tenant, default_tenant_id, list_tenants

	if tenant_header and tenant_header.strip() and tenant_header.strip() != "default":
		# Explicit header wins when set to a real tenant (still validated by caller)
		pass

	org_id = extract_org_id(claims)
	slug = extract_org_slug(claims)
	if not slug and org_id:
		slug = _org_map().get(org_id)

	if slug:
		tid = _tenant_for_slug(slug)
		known = {t.id: t for t in list_tenants()}
		if tid in known:
			return tid
		# Prefer existing tenant named after slug
		for t in known.values():
			if t.name == slug or t.id == slug:
				return t.id
		created = create_tenant(slug, notes=f"clerk:{org_id or slug}")
		return created.id

	if tenant_header and tenant_header.strip():
		return tenant_header.strip()
	return default_tenant_id()


def ensure_clerk_user(claims: dict[str, Any]):
	"""Provision / load Simulacra user from Clerk claims."""
	from .identity import (
		User,
		add_membership,
		create_user,
		get_user,
		get_user_by_email,
		list_users,
	)

	sub = str(claims.get("sub") or "")
	if not sub:
		raise PermissionError("Clerk token missing sub")
	email = (
		claims.get("email")
		or (claims.get("primary_email_address") if isinstance(claims.get("primary_email_address"), str) else None)
		or f"{sub}@users.clerk.cmul8"
	)
	email = str(email).strip().lower()
	name = str(claims.get("name") or claims.get("first_name") or email.split("@")[0])
	is_admin = sub in _super_admins()
	user_id = f"clerk_{sub}" if not sub.startswith("user_") else f"clerk_{sub}"

	# Prefer email match, then id
	existing = get_user_by_email(email)
	if existing is None:
		try:
			existing = get_user(user_id)
		except KeyError:
			existing = None
	if existing is None:
		# create_user generates usr_ ids — create then we store clerk link via email uniqueness
		user = create_user(
			email,
			password=os.urandom(24).hex(),
			name=name,
			is_platform_admin=is_admin,
		)
	else:
		user = existing
		if is_admin and not user.is_platform_admin:
			# promote in-memory only for this request unless we persist — skip mutate for now
			user = User(
				id=user.id,
				email=user.email,
				name=user.name,
				password_hash=user.password_hash,
				is_platform_admin=True,
				status=user.status,
				created_at=user.created_at,
			)

	tid = resolve_tenant_id(claims, None)
	from .identity import get_membership

	if not get_membership(tid, user.id):
		try:
			add_membership(tid, user.id, "owner" if is_admin else "member")
		except Exception as exc:  # noqa: BLE001
			log.warning("clerk membership: %s", exc)
	return user, tid
