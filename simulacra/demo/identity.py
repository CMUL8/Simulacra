"""Users, API keys, sessions, and tenant membership (RBAC)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from .paths import REPO_ROOT
from .tenants import assert_tenant_active, create_tenant, default_tenant_id, get_tenant, list_tenants

DATA_DIR = REPO_ROOT / "data"
USERS_PATH = DATA_DIR / "users.json"
KEYS_PATH = DATA_DIR / "api_keys.json"
SESSIONS_PATH = DATA_DIR / "sessions.json"
MEMBERSHIPS_PATH = DATA_DIR / "memberships.json"

Role = Literal["owner", "admin", "member", "viewer"]
ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}

# permission -> minimum role
PERMISSIONS: dict[str, Role] = {
	"project:read": "viewer",
	"project:write": "member",
	"project:approve": "member",
	"project:deploy": "admin",
	"tenant:read": "viewer",
	"tenant:manage": "admin",
	"tenant:members": "admin",
	"platform:admin": "owner",  # checked separately via is_platform_admin
}


@dataclass
class User:
	id: str
	email: str
	name: str
	password_hash: str
	is_platform_admin: bool = False
	status: str = "active"
	created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

	def public(self) -> dict[str, Any]:
		return {
			"id": self.id,
			"email": self.email,
			"name": self.name,
			"is_platform_admin": self.is_platform_admin,
			"status": self.status,
			"created_at": self.created_at,
		}


@dataclass
class Membership:
	tenant_id: str
	user_id: str
	role: Role = "member"
	created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class AuthContext:
	user: User
	tenant_id: str
	role: Role
	auth_via: str  # session | api_key | bootstrap

	def require(self, permission: str) -> None:
		if self.user.is_platform_admin:
			return
		if permission == "platform:admin":
			raise PermissionError("Platform admin required")
		needed = PERMISSIONS.get(permission, "admin")
		if ROLE_RANK.get(self.role, 0) < ROLE_RANK.get(needed, 99):
			raise PermissionError(f"Requires {needed}, have {self.role}")


def auth_required() -> bool:
	return os.environ.get("SIMULACRA_AUTH_REQUIRED", "1").lower() in ("1", "true", "yes")


def _now() -> datetime:
	return datetime.now(UTC)


def _hash_password(password: str, salt: str | None = None) -> str:
	salt = salt or secrets.token_hex(16)
	digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
	return f"pbkdf2:{salt}:{digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
	try:
		_, salt, hexdigest = stored.split(":", 2)
	except ValueError:
		return False
	digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 120_000)
	return hmac.compare_digest(digest.hex(), hexdigest)


def _hash_token(token: str) -> str:
	return hashlib.sha256(token.encode()).hexdigest()


def _load(path: Path, default: Any) -> Any:
	DATA_DIR.mkdir(parents=True, exist_ok=True)
	if not path.exists():
		path.write_text(json.dumps(default, indent=2))
		return default
	raw = path.read_text().strip()
	if not raw:
		path.write_text(json.dumps(default, indent=2))
		return default
	try:
		return json.loads(raw)
	except json.JSONDecodeError:
		# Concurrent truncate — recover default rather than 500 the request
		return default


def _save(path: Path, data: Any) -> None:
	DATA_DIR.mkdir(parents=True, exist_ok=True)
	payload = json.dumps(data, indent=2)
	tmp = path.with_suffix(path.suffix + ".tmp")
	tmp.write_text(payload)
	tmp.replace(path)


def _users() -> dict[str, Any]:
	return _load(USERS_PATH, {"users": []})


def _memberships() -> dict[str, Any]:
	return _load(MEMBERSHIPS_PATH, {"memberships": []})


def _keys() -> dict[str, Any]:
	return _load(KEYS_PATH, {"keys": []})


def _sessions() -> dict[str, Any]:
	return _load(SESSIONS_PATH, {"sessions": []})


def list_users() -> list[User]:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_list_users

		return [User(**u) for u in pg_list_users()]
	return [User(**u) for u in _users().get("users", [])]


def get_user(user_id: str) -> User:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_get_user

		raw = pg_get_user(user_id)
		if not raw:
			raise KeyError(user_id)
		return User(**raw)
	for u in list_users():
		if u.id == user_id:
			return u
	raise KeyError(user_id)


def get_user_by_email(email: str) -> User | None:
	email = email.strip().lower()
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_get_user_by_email

		raw = pg_get_user_by_email(email)
		return User(**raw) if raw else None
	for u in list_users():
		if u.email.lower() == email:
			return u
	return None


def create_user(
	email: str,
	password: str,
	*,
	name: str = "",
	is_platform_admin: bool = False,
) -> User:
	email = email.strip().lower()
	if get_user_by_email(email):
		raise ValueError("Email already registered")
	user = User(
		id=f"usr_{uuid.uuid4().hex[:12]}",
		email=email,
		name=name.strip() or email.split("@")[0],
		password_hash=_hash_password(password),
		is_platform_admin=is_platform_admin,
	)
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_insert_user

		pg_insert_user(asdict(user))
		return user
	store = _users()
	store["users"].append(asdict(user))
	_save(USERS_PATH, store)
	return user


def add_membership(tenant_id: str, user_id: str, role: Role = "member") -> Membership:
	get_tenant(tenant_id)
	get_user(user_id)
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_upsert_membership

		m = Membership(tenant_id=tenant_id, user_id=user_id, role=role)
		pg_upsert_membership(asdict(m))
		return m
	store = _memberships()
	for raw in store["memberships"]:
		if raw["tenant_id"] == tenant_id and raw["user_id"] == user_id:
			raw["role"] = role
			_save(MEMBERSHIPS_PATH, store)
			return Membership(**raw)
	m = Membership(tenant_id=tenant_id, user_id=user_id, role=role)
	store["memberships"].append(asdict(m))
	_save(MEMBERSHIPS_PATH, store)
	return m


def list_memberships(*, tenant_id: str | None = None, user_id: str | None = None) -> list[Membership]:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_list_memberships

		return [Membership(**m) for m in pg_list_memberships(tenant_id=tenant_id, user_id=user_id)]
	out: list[Membership] = []
	for raw in _memberships().get("memberships", []):
		if tenant_id and raw["tenant_id"] != tenant_id:
			continue
		if user_id and raw["user_id"] != user_id:
			continue
		out.append(Membership(**raw))
	return out


def get_membership(tenant_id: str, user_id: str) -> Membership | None:
	for m in list_memberships(tenant_id=tenant_id, user_id=user_id):
		return m
	return None


def remove_membership(tenant_id: str, user_id: str) -> None:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_delete_membership

		pg_delete_membership(tenant_id, user_id)
		return
	store = _memberships()
	store["memberships"] = [
		m
		for m in store["memberships"]
		if not (m["tenant_id"] == tenant_id and m["user_id"] == user_id)
	]
	_save(MEMBERSHIPS_PATH, store)


def create_api_key(user_id: str, *, name: str = "default", tenant_id: str | None = None) -> tuple[str, dict[str, Any]]:
	"""Returns (plaintext_key, metadata). Plaintext shown once."""
	get_user(user_id)
	raw = f"ska_{secrets.token_urlsafe(32)}"
	meta = {
		"id": f"key_{uuid.uuid4().hex[:10]}",
		"user_id": user_id,
		"tenant_id": tenant_id,
		"name": name,
		"prefix": raw[:10],
		"hash": _hash_token(raw),
		"created_at": _now().isoformat(),
		"last_used_at": None,
		"revoked": False,
	}
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_insert_api_key

		pg_insert_api_key(meta)
		return raw, meta
	store = _keys()
	store["keys"].append(meta)
	_save(KEYS_PATH, store)
	return raw, meta


def revoke_api_key(key_id: str, user_id: str | None = None) -> None:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_revoke_api_key

		pg_revoke_api_key(key_id, user_id)
		return
	store = _keys()
	for k in store["keys"]:
		if k["id"] == key_id and (user_id is None or k["user_id"] == user_id):
			k["revoked"] = True
	_save(KEYS_PATH, store)


def list_api_keys(user_id: str) -> list[dict[str, Any]]:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_list_api_keys

		return pg_list_api_keys(user_id)
	out = []
	for k in _keys().get("keys", []):
		if k["user_id"] == user_id:
			out.append({x: k[x] for x in k if x != "hash"})
	return out


def create_session(user_id: str, *, ttl_hours: int = 72) -> str:
	token = f"sst_{secrets.token_urlsafe(32)}"
	exp = _now() + timedelta(hours=ttl_hours)
	row = {
		"token_hash": _hash_token(token),
		"user_id": user_id,
		"expires_at": exp.isoformat(),
		"created_at": _now().isoformat(),
	}
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_insert_session

		pg_insert_session(row)
		return token
	store = _sessions()
	store["sessions"].append(row)
	store["sessions"] = [
		s for s in store["sessions"] if datetime.fromisoformat(s["expires_at"]) > _now()
	]
	_save(SESSIONS_PATH, store)
	return token


def _user_from_token(token: str) -> tuple[User, str] | None:
	token = token.strip()
	if not token:
		return None
	th = _hash_token(token)
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_find_api_key_by_hash, pg_find_session, pg_touch_api_key

		if token.startswith("ska_"):
			k = pg_find_api_key_by_hash(th)
			if not k:
				return None
			pg_touch_api_key(k["id"])
			return get_user(k["user_id"]), "api_key"
		s = pg_find_session(th)
		if not s:
			return None
		return get_user(s["user_id"]), "session"

	if token.startswith("ska_"):
		store = _keys()
		for k in store.get("keys", []):
			if k.get("revoked"):
				continue
			if hmac.compare_digest(k["hash"], th):
				k["last_used_at"] = _now().isoformat()
				_save(KEYS_PATH, store)
				return get_user(k["user_id"]), "api_key"
		return None

	for s in _sessions().get("sessions", []):
		if datetime.fromisoformat(s["expires_at"]) < _now():
			continue
		if hmac.compare_digest(s["token_hash"], th):
			return get_user(s["user_id"]), "session"
	return None


def resolve_auth(
	authorization: str | None,
	*,
	tenant_header: str | None,
) -> AuthContext:
	"""Resolve Bearer token + tenant into AuthContext. Raises PermissionError/ValueError."""
	from .tenants import list_tenants as _lt

	# Ensure store exists
	_lt()

	if not authorization or not authorization.lower().startswith("bearer "):
		if not auth_required():
			return _bootstrap_dev_context(tenant_header)
		raise PermissionError("Authentication required")

	token = authorization.split(" ", 1)[1].strip()
	# Clerk session JWTs (when CMUL8 Clerk is configured)
	if not token.startswith("sst_") and not token.startswith("ska_"):
		from .clerk_auth import clerk_enabled, ensure_clerk_user, verify_clerk_jwt

		if clerk_enabled():
			claims = verify_clerk_jwt(token)
			user, tid_from_org = ensure_clerk_user(claims)
			header = (tenant_header or "").strip()
			# Prefer org mapping when client still has stale "default"
			chosen = header
			if not chosen or (chosen == default_tenant_id() and not get_membership(chosen, user.id)):
				chosen = tid_from_org or header
			return _auth_context_for_user(
				user,
				tenant_header=chosen or "",
				auth_via="clerk",
				allow_stale_header=True,
			)

	found = _user_from_token(token)
	if not found:
		raise PermissionError("Invalid or expired credentials")
	user, via = found
	if user.status != "active":
		raise PermissionError("User suspended")

	return _auth_context_for_user(
		user,
		tenant_header=(tenant_header or "").strip(),
		auth_via=via,
		allow_stale_header=True,
	)


def _auth_context_for_user(
	user: User,
	*,
	tenant_header: str,
	auth_via: str,
	allow_stale_header: bool = True,
) -> AuthContext:
	"""Pick a tenant the user can actually access.

	Stale ``X-Tenant-Id: default`` from the console used to 403 members of other
	workspaces and clear their session — landing then showed zero project cards.
	"""
	tid = (tenant_header or "").strip()
	if tid == "*":
		if not user.is_platform_admin:
			raise PermissionError("Platform admin required for cross-tenant access")
		return AuthContext(user=user, tenant_id="*", role="owner", auth_via=auth_via)

	mine = list_memberships(user_id=user.id)
	if user.is_platform_admin:
		if not tid:
			tid = default_tenant_id()
		assert_tenant_active(tid)
		return AuthContext(user=user, tenant_id=tid, role="owner", auth_via=auth_via)

	if tid:
		membership = get_membership(tid, user.id)
		if membership:
			assert_tenant_active(tid)
			return AuthContext(user=user, tenant_id=tid, role=membership.role, auth_via=auth_via)
		if not allow_stale_header or not mine:
			raise PermissionError(f"Not a member of tenant {tid}")

	if not mine:
		raise PermissionError("No tenant membership")
	m0 = mine[0]
	assert_tenant_active(m0.tenant_id)
	return AuthContext(user=user, tenant_id=m0.tenant_id, role=m0.role, auth_via=auth_via)


def _bootstrap_dev_context(tenant_header: str | None) -> AuthContext:
	"""When auth is optional, use/create local bootstrap admin."""
	ensure_bootstrap()
	user = get_user_by_email(os.environ.get("SIMULACRA_BOOTSTRAP_EMAIL", "admin@localhost"))
	if user is None:
		user = list_users()[0]
	tid = (tenant_header or default_tenant_id()).strip() or default_tenant_id()
	if tid != "*":
		assert_tenant_active(tid)
	return AuthContext(user=user, tenant_id=tid if tid != "*" else default_tenant_id(), role="owner", auth_via="bootstrap")


def ensure_bootstrap() -> dict[str, Any]:
	"""Create default tenant + platform admin on first boot."""
	from .db import migrate, using_postgres

	migrate()
	list_tenants()  # ensure default tenant
	email = os.environ.get("SIMULACRA_BOOTSTRAP_EMAIL", "admin@localhost").strip().lower()
	password = os.environ.get("SIMULACRA_BOOTSTRAP_PASSWORD", "simulacra-admin-change-me")
	info: dict[str, Any] = {"bootstrapped": False, "identity_backend": "postgres" if using_postgres() else "json"}
	user = get_user_by_email(email)
	if user is None:
		user = create_user(email, password, name="Platform Admin", is_platform_admin=True)
		add_membership(default_tenant_id(), user.id, "owner")
		raw_key, meta = create_api_key(user.id, name="bootstrap", tenant_id=default_tenant_id())
		boot = {
			"email": email,
			"password": password if password == "simulacra-admin-change-me" else "(from env)",
			"api_key": raw_key,
			"key_id": meta["id"],
			"created_at": _now().isoformat(),
			"warning": "Change SIMULACRA_BOOTSTRAP_PASSWORD and rotate API key for production",
			"identity_backend": info["identity_backend"],
		}
		DATA_DIR.mkdir(parents=True, exist_ok=True)
		(DATA_DIR / "bootstrap.json").write_text(json.dumps(boot, indent=2))
		info = {
			"bootstrapped": True,
			"email": email,
			"api_key_prefix": raw_key[:10],
			"identity_backend": info["identity_backend"],
		}
	else:
		if not get_membership(default_tenant_id(), user.id):
			add_membership(default_tenant_id(), user.id, "owner")
		info = {"bootstrapped": False, "email": email, "identity_backend": info["identity_backend"]}
	return info


def register_user(
	email: str,
	password: str,
	*,
	name: str = "",
	tenant_name: str | None = None,
	invite_tenant_id: str | None = None,
) -> tuple[User, str]:
	"""Sign up: create user + own tenant (or join invite tenant as member). Returns user + session."""
	user = create_user(email, password, name=name)
	if invite_tenant_id:
		assert_tenant_active(invite_tenant_id)
		add_membership(invite_tenant_id, user.id, "member")
	else:
		tenant = create_tenant(tenant_name or f"{user.name}'s workspace")
		add_membership(tenant.id, user.id, "owner")
	token = create_session(user.id)
	return user, token


def login_user(email: str, password: str) -> tuple[User, str]:
	user = get_user_by_email(email)
	if not user or not _verify_password(password, user.password_hash):
		raise PermissionError("Invalid email or password")
	if user.status != "active":
		raise PermissionError("User suspended")
	return user, create_session(user.id)


def user_tenants(user: User) -> list[dict[str, Any]]:
	if user.is_platform_admin:
		return [{**t.to_dict(), "role": "owner"} for t in list_tenants()]
	out = []
	for m in list_memberships(user_id=user.id):
		try:
			t = get_tenant(m.tenant_id)
		except KeyError:
			continue
		out.append({**t.to_dict(), "role": m.role})
	return out
