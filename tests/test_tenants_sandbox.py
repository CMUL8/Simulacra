from simulacra.demo.sandbox import resolve_mode, sandbox_status
from simulacra.demo.tenants import create_tenant, default_tenant_id, get_tenant, list_tenants


def test_default_tenant_exists():
	ids = {t.id for t in list_tenants()}
	assert default_tenant_id() in ids


def test_create_tenant_with_policy():
	t = create_tenant("Test Co", policy={"sandbox": "worktree", "max_concurrent_jobs": 1})
	loaded = get_tenant(t.id)
	assert loaded.name == "Test Co"
	assert loaded.policy.sandbox == "worktree"
	assert loaded.policy.max_concurrent_jobs == 1


def test_sandbox_auto_resolves():
	status = sandbox_status()
	assert status["active"] in ("docker", "worktree")
	assert resolve_mode("auto") == status["active"]
