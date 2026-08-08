#!/usr/bin/env python3
"""Live E2E: slides (presentation) modality — create → Built → iterate."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://simulacra-production-b0d9.up.railway.app"
OUT = Path("/tmp/simulacra-e2e-slides")
faults: list[str] = []
passes: list[str] = []


def note(ok: bool, msg: str, detail: str = "") -> None:
	line = f"{'PASS' if ok else 'FAULT'}: {msg}"
	if detail:
		line += f" — {detail[:300]}"
	print(line, flush=True)
	(passes if ok else faults).append(msg if ok else f"{msg} | {detail[:200]}")


def api(method: str, path: str, token: str, tenant: str, body: dict | None = None, timeout: float = 120):
	data = None if body is None else json.dumps(body).encode()
	headers = {
		"Authorization": f"Bearer {token}",
		"X-Tenant-Id": tenant,
		"Accept": "application/json",
	}
	if body is not None:
		headers["Content-Type"] = "application/json"
	req = urllib.request.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
	try:
		with urllib.request.urlopen(req, timeout=timeout) as res:
			raw = res.read().decode()
			return res.status, json.loads(raw) if raw else {}
	except urllib.error.HTTPError as e:
		raw = e.read().decode()
		try:
			payload = json.loads(raw)
		except Exception:
			payload = {"detail": raw[:500]}
		return e.code, payload
	except Exception as e:
		return 0, {"detail": str(e)}


def wait_idle(token: str, tenant: str, pid: str, timeout: float = 540, *, expect_phase: str | None = None) -> dict:
	deadline = time.time() + timeout
	last: dict = {}
	stable = 0
	while time.time() < deadline:
		_, snap = api("GET", f"/projects/{pid}", token, tenant)
		_, jobinfo = api("GET", f"/projects/{pid}/job", token, tenant)
		last = snap
		job = jobinfo.get("job") or snap.get("job") or {}
		status = job.get("status") or "idle"
		live = bool(jobinfo.get("live"))
		proj = snap.get("project") or {}
		phase = proj.get("phase")
		pstatus = proj.get("status")
		print(
			f"  … live={live} job={status}/{job.get('kind')} phase={phase} "
			f"status={pstatus} kind={proj.get('artifact_kind')} "
			f"source={(proj.get('prime') or {}).get('source')} preview={bool(snap.get('preview_url'))}",
			flush=True,
		)
		building = pstatus in (
			"building_app",
			"publishing_preview",
			"extracting",
			"gating",
			"updating",
			"planning",
		) or (phase == "build")
		terminal = (not live) and status in ("idle", "failed", "cancelled")
		if expect_phase:
			done = terminal and phase == expect_phase and not building
		else:
			done = terminal and not building
		if done:
			stable += 1
			if stable >= 2:
				return snap
		else:
			stable = 0
		time.sleep(4)
	note(False, "Timed out waiting for idle job")
	return last


def main() -> int:
	OUT.mkdir(parents=True, exist_ok=True)
	email = "e2e-1786188418@cmul8.test"
	password = "E2eTestPass123!"
	code, sess = api("POST", "/auth/login", "", "default", {"email": email, "password": password})
	note(code == 200 and bool(sess.get("token")), "Login", f"status={code}")
	if code != 200:
		return 1
	token, tenant = sess["token"], sess["tenant_id"]

	# health / formats
	_, health = api("GET", "/health", token, tenant)
	note(True, "Health", f"version={health.get('version')}")
	code, formats = api("GET", "/formats", token, tenant)
	kinds = {f.get("kind") for f in (formats.get("formats") or [])}
	note(code == 200 and "slides" in kinds, "Formats include slides", f"status={code} kinds={sorted(kinds)}")

	code, snap = api(
		"POST",
		"/projects",
		token,
		tenant,
		{
			"prompt": "A board deck on vendor risk from our diligence pack — title, KPIs, risk mix, top vendors, ask",
			"goal": "Vendor risk board presentation",
			"use_fixture": True,
			"artifact_kind": "slides",
		},
	)
	note(code == 200 and bool(snap.get("project")), "Create slides project", f"status={code} detail={snap.get('detail')}")
	if code != 200:
		return 1
	pid = snap["project"]["id"]
	(OUT / "pid.txt").write_text(pid)
	note(snap["project"].get("artifact_kind") == "slides", "artifact_kind=slides", str(snap["project"].get("artifact_kind")))

	snap = wait_idle(token, tenant, pid, 600, expect_phase="ready")
	proj = snap.get("project") or {}
	prime = proj.get("prime") or {}
	source = prime.get("source")
	note(proj.get("phase") == "ready", "Create lands ready", f"phase={proj.get('phase')} status={proj.get('status')}")
	note(
		source in ("prime", "craft"),
		"Presentation Built",
		f"source={source} err={prime.get('last_error')}",
	)
	note(proj.get("artifact_kind") == "slides", "Still slides kind", str(proj.get("artifact_kind")))
	preview = snap.get("preview_url")
	note(bool(preview) and "127.0.0.1" not in str(preview), "Preview URL", str(preview))

	if preview:
		path = preview if str(preview).startswith("/") else f"/projects/{pid}/preview/"
		try:
			with urllib.request.urlopen(f"{BASE}{path}", timeout=30) as res:
				html = res.read().decode("utf-8", "replace")
			note(res.status == 200, "Preview HTML OK", f"bytes={len(html)}")
			cfg_url = f"{BASE}{path.rstrip('/')}/config.json"
			with urllib.request.urlopen(cfg_url, timeout=30) as res:
				cfg = json.loads(res.read().decode())
			note(cfg.get("artifactKind") == "slides" or cfg.get("layout") == "deck", "config is deck", json.dumps(cfg)[:160])
		except Exception as e:
			note(False, "Preview assets", str(e))

	# Iterate — aesthetic / structure change on slides
	n_before = len(proj.get("chat") or [])
	code, _ = api(
		"POST",
		f"/projects/{pid}/chat",
		token,
		tenant,
		{
			"message": (
				"Make the title slide punchier and ensure Top vendors slide shows five names "
				"with max scores. Keep full-bleed slides. Edit src/App.tsx."
			),
		},
	)
	note(code in (200, 202), "Slides iterate accepted", f"status={code}")
	snap = wait_idle(token, tenant, pid, 480)
	proj = snap.get("project") or {}
	note(len(proj.get("chat") or []) > n_before, "Iterate added chat")
	last = [m for m in (proj.get("chat") or []) if m.get("role") == "assistant"][-1]
	note(
		last.get("source") in ("prime", "craft", "heuristic", "error", "system"),
		"Iterate sourced",
		f"source={last.get('source')} {(last.get('content') or '')[:140]}",
	)
	note(proj.get("phase") == "ready", "Still ready after iterate", f"phase={proj.get('phase')}")

	report = {
		"project_id": pid,
		"passes": passes,
		"faults": faults,
		"artifact_kind": proj.get("artifact_kind"),
		"source": (proj.get("prime") or {}).get("source"),
		"preview_url": snap.get("preview_url"),
	}
	(OUT / "report.json").write_text(json.dumps(report, indent=2))
	print("\n=== SLIDES SUMMARY ===", flush=True)
	print(f"project={pid} passes={len(passes)} faults={len(faults)}", flush=True)
	return 1 if faults else 0


if __name__ == "__main__":
	sys.exit(main())
