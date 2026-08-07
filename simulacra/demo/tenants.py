"""Multi-tenant registry and policy."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .paths import REPO_ROOT

TENANTS_PATH = REPO_ROOT / "tenants" / "tenants.json"


@dataclass
class TenantPolicy:
	sandbox: str = "auto"  # auto | docker | worktree
	network: str = "deny"  # deny | allowlist
	max_concurrent_jobs: int = 2
	max_projects: int = 50
	max_jobs_per_day: int = 100
	allowed_models: list[str] = field(default_factory=lambda: ["anthropic/claude-3-haiku"])
	retention_days: int = 30
	require_approve: bool = True
	sso_enforced: bool = False



@dataclass
class Tenant:
	id: str
	name: str
	created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
	status: str = "active"  # active | suspended
	policy: TenantPolicy = field(default_factory=TenantPolicy)
	notes: str = ""

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)

	@classmethod
	def from_dict(cls, data: dict[str, Any]) -> Tenant:
		pol = data.get("policy") or {}
		return cls(
			id=data["id"],
			name=data.get("name") or data["id"],
			created_at=data.get("created_at") or datetime.now(UTC).isoformat(),
			status=data.get("status", "active"),
			policy=TenantPolicy(
				sandbox=pol.get("sandbox", "auto"),
				network=pol.get("network", "deny"),
				max_concurrent_jobs=int(pol.get("max_concurrent_jobs", 2)),
				max_projects=int(pol.get("max_projects", 50)),
				max_jobs_per_day=int(pol.get("max_jobs_per_day", 100)),
				allowed_models=list(pol.get("allowed_models") or ["anthropic/claude-3-haiku"]),
				retention_days=int(pol.get("retention_days", 30)),
				require_approve=bool(pol.get("require_approve", True)),
				sso_enforced=bool(pol.get("sso_enforced", False)),
			),
			notes=data.get("notes", ""),
		)


def default_tenant_id() -> str:
	return os.environ.get("SIMULACRA_DEFAULT_TENANT", "default")


def _ensure_store() -> dict[str, Any]:
	TENANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
	if not TENANTS_PATH.exists():
		tid = default_tenant_id()
		store = {
			"version": 1,
			"tenants": [
				Tenant(id=tid, name="Default", notes="Built-in tenant").to_dict(),
			],
		}
		TENANTS_PATH.write_text(json.dumps(store, indent=2))
		return store
	return json.loads(TENANTS_PATH.read_text())


def _save_store(store: dict[str, Any]) -> None:
	TENANTS_PATH.parent.mkdir(parents=True, exist_ok=True)
	TENANTS_PATH.write_text(json.dumps(store, indent=2))


def _ensure_default_pg() -> None:
	from .db import migrate
	from .pg_store import pg_list_tenants, pg_upsert_tenant

	migrate()
	existing = pg_list_tenants()
	if not any(t["id"] == default_tenant_id() for t in existing):
		t = Tenant(id=default_tenant_id(), name="Default", notes="Built-in tenant")
		pg_upsert_tenant(t.to_dict())


def list_tenants() -> list[Tenant]:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_list_tenants

		_ensure_default_pg()
		return [Tenant.from_dict(t) for t in pg_list_tenants()]
	store = _ensure_store()
	return [Tenant.from_dict(t) for t in store.get("tenants", [])]


def get_tenant(tenant_id: str) -> Tenant:
	for t in list_tenants():
		if t.id == tenant_id:
			return t
	raise KeyError(f"Unknown tenant: {tenant_id}")


def create_tenant(name: str, *, policy: dict[str, Any] | None = None, notes: str = "") -> Tenant:
	tid = f"ten_{uuid.uuid4().hex[:10]}"
	tenant = Tenant(id=tid, name=name.strip() or tid, notes=notes)
	if policy:
		tenant.policy = Tenant.from_dict({**tenant.to_dict(), "policy": policy}).policy
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_upsert_tenant

		_ensure_default_pg()
		pg_upsert_tenant(tenant.to_dict())
		return tenant
	store = _ensure_store()
	store.setdefault("tenants", []).append(tenant.to_dict())
	_save_store(store)
	return tenant


def update_tenant(tenant_id: str, *, name: str | None = None, status: str | None = None, policy: dict[str, Any] | None = None, notes: str | None = None) -> Tenant:
	from .db import using_postgres

	if using_postgres():
		from .pg_store import pg_update_tenant

		_ensure_default_pg()
		patch: dict[str, Any] = {}
		if name is not None:
			patch["name"] = name
		if status is not None:
			patch["status"] = status
		if notes is not None:
			patch["notes"] = notes
		if policy is not None:
			patch["policy"] = policy
		raw = pg_update_tenant(tenant_id, patch)
		if raw is None:
			raise KeyError(f"Unknown tenant: {tenant_id}")
		return Tenant.from_dict(raw)
	store = _ensure_store()
	found = None
	for i, raw in enumerate(store.get("tenants", [])):
		if raw["id"] == tenant_id:
			if name is not None:
				raw["name"] = name
			if status is not None:
				raw["status"] = status
			if notes is not None:
				raw["notes"] = notes
			if policy is not None:
				raw["policy"] = {**(raw.get("policy") or {}), **policy}
			store["tenants"][i] = raw
			found = Tenant.from_dict(raw)
			break
	if found is None:
		raise KeyError(f"Unknown tenant: {tenant_id}")
	_save_store(store)
	return found


def assert_tenant_active(tenant_id: str) -> Tenant:
	tenant = get_tenant(tenant_id)
	if tenant.status != "active":
		raise PermissionError(f"Tenant {tenant_id} is {tenant.status}")
	return tenant


def assert_under_project_quota(tenant_id: str) -> Tenant:
	from .runs import list_projects

	tenant = assert_tenant_active(tenant_id)
	count = len(list_projects(tenant_id=tenant_id))
	if count >= tenant.policy.max_projects:
		raise PermissionError(
			f"Tenant project quota exceeded ({count}/{tenant.policy.max_projects})"
		)
	return tenant


def admin_overview(*, for_tenant_id: str | None = None) -> dict[str, Any]:
	from .runs import list_projects

	tenants = list_tenants()
	if for_tenant_id and for_tenant_id != "*":
		tenants = [t for t in tenants if t.id == for_tenant_id]
	projects = list_projects() if not for_tenant_id or for_tenant_id == "*" else list_projects(tenant_id=for_tenant_id)
	by_tenant: dict[str, int] = {}
	for p in projects:
		tid = getattr(p, "tenant_id", None) or default_tenant_id()
		by_tenant[tid] = by_tenant.get(tid, 0) + 1
	return {
		"tenants": [
			{
				**t.to_dict(),
				"project_count": by_tenant.get(t.id, 0),
			}
			for t in tenants
		],
		"sandbox_mode": os.environ.get("SIMULACRA_SANDBOX", "auto"),
		"default_tenant": default_tenant_id(),
		"totals": {
			"tenants": len(tenants),
			"projects": len(projects),
			"active_tenants": sum(1 for t in tenants if t.status == "active"),
		},
	}
