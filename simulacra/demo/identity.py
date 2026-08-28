"""Users, API keys, sessions, and tenant membership (RBAC)."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal

import fcntl

from .paths import REPO_ROOT
from .tenants import assert_tenant_active, create_tenant, default_tenant_id, get_tenant, list_tenants

DATA_DIR = Path(os.environ.get("SIMULACRA_DATA_DIR", REPO_ROOT / "data")).resolve()
USERS_PATH = DATA_DIR / "users.json"
KEYS_PATH = DATA_DIR / "api_keys.json"
SESSIONS_PATH = DATA_DIR / "sessions.json"
MEMBERSHIPS_PATH = DATA_DIR / "memberships.json"
RESET_TOKENS_PATH = DATA_DIR / "password_reset_tokens.json"

Role = Literal["owner", "admin", "member", "viewer"]
ROLE_RANK = {"viewer": 1, "member": 2, "admin": 3, "owner": 4}

# permission -> minimum role
PERMISSIONS: dict[str, Role] = {
	"project:read": "viewer",
	"project:write": "member",
	"project:approve": "admin",
	"project:deploy": "admin",
	"tenant:read": "viewer",
	"tenant:manage": "admin",
	"tenant:members": "admin",
	"platform:admin": "owner",  # checked separately via is_platform_admin
}

_MEMBERSHIP_LOCKS: dict[str, threading.RLock] = {}
_MEMBERSHIP_LOCKS_GUARD = threading.Lock()
_MEMBERSHIP_LOCK_DEPTH = threading.local()


@dataclass
class User:
	id: str
	email: str
	name: str
	password_hash: str
	is_platform_admin: bool = False
	status: str = "active"
	created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	avatar_url: str | None = None
	verified_email: str | None = None
	verified_email_at: str | None = None
	provider_subject: str | None = None

	def public(self) -> dict[str, Any]:
		return {
			"id": self.id,
			"email": self.email,
			"name": self.name,
			"is_platform_admin": self.is_platform_admin,
			"status": self.status,
			"created_at": self.created_at,
			"avatar_url": self.avatar_url,
		}


@dataclass
class Membership:
	tenant_id: str
	user_id: str
	role: Role = "member"
	created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	transaction_id: str | None = None
	visibility_state: str = "committed"


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


@contextmanager
def membership_store_lock(tenant_id: str, user_id: str) -> Iterator[None]:
	"""Serialize one tenant/user membership across JSON or PostgreSQL writers.

	The acceptance coordinator holds this lock while it publishes the matching
	room child. Ordinary membership writers use the same lock, so authority
	cannot change between the admission precheck and its collaboration write.
	"""
	key = f"{tenant_id}\0{user_id}"
	depths = getattr(_MEMBERSHIP_LOCK_DEPTH, "depths", {})
	if depths.get(key, 0):
		depths[key] += 1
		_MEMBERSHIP_LOCK_DEPTH.depths = depths
		try:
			yield
		finally:
			depths[key] -= 1
		return
	with _MEMBERSHIP_LOCKS_GUARD:
		local = _MEMBERSHIP_LOCKS.setdefault(key, threading.RLock())
	with local:
		from .db import using_postgres
		if using_postgres():
			# Session advisory locks coordinate all application instances while
			# the actual CRUD helpers keep their existing short transactions.
			from .db import connection
			lock_key = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
			with connection() as conn:
				conn.execute("SELECT pg_advisory_lock(%s)", (lock_key,))
				depths[key] = 1
				_MEMBERSHIP_LOCK_DEPTH.depths = depths
				try:
					yield
				finally:
					depths.pop(key, None)
					conn.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
			return
		lock_dir = DATA_DIR / ".identity-membership-locks"
		lock_dir.mkdir(parents=True, exist_ok=True)
		lock_path = lock_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.lock"
		fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
		try:
			fcntl.flock(fd, fcntl.LOCK_EX)
			depths[key] = 1
			_MEMBERSHIP_LOCK_DEPTH.depths = depths
			try:
				yield
			finally:
				depths.pop(key, None)
		finally:
			fcntl.flock(fd, fcntl.LOCK_UN)
			os.close(fd)


def get_membership_record(tenant_id: str, user_id: str) -> Membership | None:
	"""Coordinator-only raw membership lookup, including hidden rows."""
	from .db import using_postgres
	if using_postgres():
		from .pg_store import pg_list_memberships
		rows = pg_list_memberships(tenant_id=tenant_id, user_id=user_id)
	else:
		rows = [
			row for row in _memberships().get("memberships", [])
			if row.get("tenant_id") == tenant_id and row.get("user_id") == user_id
		]
	if not rows:
		return None
	row = dict(rows[0])
	row.setdefault("transaction_id", None)
	row.setdefault("visibility_state", "committed")
	return Membership(**row)


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
	avatar_url: str | None = None,
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
		avatar_url=avatar_url,
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


def add_membership(tenant_id: str, user_id: str, role: Role = "member", *, transaction_id: str | None = None,
	visibility_state: str = "committed") -> Membership:
	with membership_store_lock(tenant_id, user_id):
		get_tenant(tenant_id)
		get_user(user_id)
		from .db import using_postgres

		if using_postgres():
			from .pg_store import pg_upsert_membership

			m = Membership(tenant_id=tenant_id, user_id=user_id, role=role, transaction_id=transaction_id, visibility_state=visibility_state)
			pg_upsert_membership(asdict(m))
			return m
		store = _memberships()
		for raw in store["memberships"]:
			if raw["tenant_id"] == tenant_id and raw["user_id"] == user_id:
				raw["role"] = role
				raw["transaction_id"] = transaction_id
				raw["visibility_state"] = visibility_state
				_save(MEMBERSHIPS_PATH, store)
				return Membership(**raw)
		m = Membership(tenant_id=tenant_id, user_id=user_id, role=role, transaction_id=transaction_id, visibility_state=visibility_state)
		store["memberships"].append(asdict(m))
		_save(MEMBERSHIPS_PATH, store)
		return m


def list_memberships(*, tenant_id: str | None = None, user_id: str | None = None) -> list[Membership]:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_list_memberships

		return [membership for membership in (Membership(**m) for m in pg_list_memberships(tenant_id=tenant_id, user_id=user_id))
			if _membership_visible(membership)]
	out: list[Membership] = []
	for raw in _memberships().get("memberships", []):
		if tenant_id and raw["tenant_id"] != tenant_id:
			continue
		if user_id and raw["user_id"] != user_id:
			continue
		row = dict(raw)
		row.setdefault("transaction_id", None)
		row.setdefault("visibility_state", "committed")
		membership = Membership(**row)
		if _membership_visible(membership):
			out.append(membership)
	return out


def get_membership(tenant_id: str, user_id: str) -> Membership | None:
	for m in list_memberships(tenant_id=tenant_id, user_id=user_id):
		return m
	return None


def _membership_visible(membership: Membership) -> bool:
	if membership.transaction_id is None:
		return True
	if membership.visibility_state != "committed":
		return False
	try:
		from simulacra.collaboration.invitation_acceptance import is_acceptance_complete_for_tenant
		return is_acceptance_complete_for_tenant(membership.tenant_id, membership.transaction_id)
	except Exception:
		return False


def update_membership_visibility(tenant_id: str, user_id: str, transaction_id: str, visibility_state: str) -> None:
	"""Internal coordinator mutation; normal membership readers remain filtered."""
	with membership_store_lock(tenant_id, user_id):
		from .db import using_postgres
		if using_postgres():
			from .pg_store import pg_update_membership_visibility
			pg_update_membership_visibility(tenant_id, user_id, transaction_id, visibility_state)
			return
		store = _memberships()
		for row in store["memberships"]:
			if row.get("tenant_id") == tenant_id and row.get("user_id") == user_id and row.get("transaction_id") == transaction_id:
				row["visibility_state"] = visibility_state
				_save(MEMBERSHIPS_PATH, store)
				return
		raise KeyError(user_id)


def record_verified_provider_identity(user_id: str, provider_subject: str, verified_email: str) -> User:
	"""Persist server-verified provider proof without granting a membership."""
	verified_email = verified_email.strip().lower()
	from .db import using_postgres
	if using_postgres():
		from .pg_store import pg_record_verified_provider_identity
		return User(**pg_record_verified_provider_identity(user_id, provider_subject, verified_email))
	store = _users()
	for row in store["users"]:
		if row["id"] == user_id:
			if row.get("provider_subject") not in {None, provider_subject}:
				raise PermissionError("verified identity subject mismatch")
			row["provider_subject"] = provider_subject
			row["verified_email"] = verified_email
			row["verified_email_at"] = _now().isoformat()
			_save(USERS_PATH, store)
			return User(**row)
	raise KeyError(user_id)


def remove_membership(tenant_id: str, user_id: str) -> None:
	with membership_store_lock(tenant_id, user_id):
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
		# Password sign-up no longer needs a workspace field. Keep the legacy
		# argument for API compatibility, but always produce a bounded personal name.
		personal_name = (tenant_name or f"{user.name}'s Mission").strip()[:120]
		tenant = create_tenant(personal_name or "My Mission")
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


def update_password(user_id: str, new_password: str) -> User:
	"""Set a new password hash for an existing user."""
	if len(new_password) < 8:
		raise ValueError("Password must be at least 8 characters")
	user = get_user(user_id)
	if user is None:
		raise KeyError(f"Unknown user {user_id}")
	hashed = _hash_password(new_password)
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_update_user_password

		pg_update_user_password(user_id, hashed)
	else:
		store = _users()
		for raw in store.get("users", []):
			if raw["id"] == user_id:
				raw["password_hash"] = hashed
				break
		_save(USERS_PATH, store)
	user.password_hash = hashed
	return user


def _revoke_sessions(user_id: str) -> None:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_delete_sessions_for_user

		pg_delete_sessions_for_user(user_id)
		return
	store = _sessions()
	store["sessions"] = [s for s in store.get("sessions", []) if s.get("user_id") != user_id]
	_save(SESSIONS_PATH, store)


def _reset_store() -> dict[str, Any]:
	return _load(RESET_TOKENS_PATH, {"tokens": []})


def public_app_origin() -> str:
	explicit = (os.environ.get("SIMULACRA_PUBLIC_URL") or "").strip().rstrip("/")
	if explicit:
		return explicit
	domain = (os.environ.get("RAILWAY_PUBLIC_DOMAIN") or "").strip().rstrip("/")
	if domain:
		if domain.startswith("http://") or domain.startswith("https://"):
			return domain
		return f"https://{domain}"
	return ""


def create_password_reset(user_id: str, *, ttl_minutes: int = 60) -> str:
	"""Issue a single-use password reset token (plaintext returned once)."""
	user = get_user(user_id)
	if user is None:
		raise KeyError(f"Unknown user {user_id}")
	if user.status != "active":
		raise PermissionError("User suspended")
	token = f"spr_{secrets.token_urlsafe(32)}"
	exp = _now() + timedelta(minutes=ttl_minutes)
	row = {
		"token_hash": _hash_token(token),
		"user_id": user_id,
		"expires_at": exp.isoformat(),
		"used_at": None,
		"created_at": _now().isoformat(),
	}
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_insert_reset_token

		pg_insert_reset_token(row)
		return token
	store = _reset_store()
	# Drop expired / used
	fresh = []
	for t in store.get("tokens", []):
		if t.get("used_at"):
			continue
		if datetime.fromisoformat(t["expires_at"]) <= _now():
			continue
		fresh.append(t)
	fresh.append(row)
	store["tokens"] = fresh
	_save(RESET_TOKENS_PATH, store)
	return token


def request_password_reset(email: str) -> dict[str, Any]:
	"""Start reset for an email. Always ok=True; include reset_url when account exists.

	No email sender yet — the console shows the one-time link inline.
	"""
	out: dict[str, Any] = {"ok": True, "expires_in_minutes": 60}
	user = get_user_by_email(email)
	if user is None or user.status != "active":
		return out
	token = create_password_reset(user.id, ttl_minutes=60)
	origin = public_app_origin()
	out["token"] = token
	out["reset_url"] = f"{origin}/#reset={token}" if origin else f"#reset={token}"
	return out


def reset_password_with_token(token: str, new_password: str) -> User:
	"""Consume a reset token and set a new password. Invalidates other sessions."""
	token = (token or "").strip()
	if not token.startswith("spr_"):
		raise PermissionError("Invalid or expired reset link")
	if len(new_password) < 8:
		raise ValueError("Password must be at least 8 characters")
	th = _hash_token(token)
	from .db import using_postgres

	row: dict[str, Any] | None = None
	if using_postgres():
		from .pg_store import pg_find_reset_token, pg_mark_reset_used

		row = pg_find_reset_token(th)
		if not row or row.get("used_at"):
			raise PermissionError("Invalid or expired reset link")
		if datetime.fromisoformat(row["expires_at"]) <= _now():
			raise PermissionError("Invalid or expired reset link")
		user = update_password(row["user_id"], new_password)
		pg_mark_reset_used(th)
		_revoke_sessions(user.id)
		return user

	store = _reset_store()
	for t in store.get("tokens", []):
		if not hmac.compare_digest(t.get("token_hash", ""), th):
			continue
		row = t
		break
	if not row or row.get("used_at"):
		raise PermissionError("Invalid or expired reset link")
	if datetime.fromisoformat(row["expires_at"]) <= _now():
		raise PermissionError("Invalid or expired reset link")
	user = update_password(row["user_id"], new_password)
	row["used_at"] = _now().isoformat()
	_save(RESET_TOKENS_PATH, store)
	_revoke_sessions(user.id)
	return user


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
