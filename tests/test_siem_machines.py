"""SIEM formatting + gVisor/machine sandbox resolution tests."""

from __future__ import annotations

from simulacra.demo.sandbox import resolve_mode, sandbox_status
from simulacra.demo.siem import export_bundle, format_events, to_cef, to_hec


def test_sandbox_auto_resolves(monkeypatch):
	monkeypatch.delenv("SIMULACRA_SANDBOX", raising=False)
	status = sandbox_status()
	assert status["active"] in ("docker", "worktree", "gvisor", "machine")
	assert resolve_mode("auto") == status["active"]
	assert "gvisor_available" in status
	assert "machines" in status


def test_resolve_gvisor_falls_back(monkeypatch):
	monkeypatch.setenv("SIMULACRA_SANDBOX", "gvisor")
	monkeypatch.setattr("simulacra.demo.sandbox.gvisor_available", lambda: False)
	monkeypatch.setattr("simulacra.demo.sandbox.docker_available", lambda: False)
	assert resolve_mode("gvisor") == "worktree"


def test_resolve_machine_when_docker(monkeypatch):
	monkeypatch.setenv("SIMULACRA_SANDBOX", "machine")
	monkeypatch.setattr("simulacra.demo.sandbox.docker_available", lambda: True)
	assert resolve_mode("machine") == "machine"


def test_cef_and_hec_export():
	evt = {
		"id": "aud_test",
		"ts": "2026-08-07T00:00:00+00:00",
		"action": "project.create",
		"tenant_id": "ten_x",
		"user_id": "usr_y",
		"resource": "/projects",
		"status": "ok",
		"detail": {"project_id": "proj_1"},
	}
	cef = to_cef(evt)
	assert cef.startswith("CEF:0|CMUL8|Simulacra|")
	assert "project.create" in cef
	hec = to_hec(evt)
	assert hec["sourcetype"] == "simulacra:audit"
	assert hec["event"]["id"] == "aud_test"
	body = format_events([evt], "json")
	assert '"aud_test"' in body
	bundle = export_bundle([evt], fmt="cef")
	assert bundle["count"] == 1
	assert "CEF:0" in bundle["body"]
