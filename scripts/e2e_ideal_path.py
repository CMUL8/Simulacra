#!/usr/bin/env python3
"""Ideal-path E2E against live Simulacra."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "https://simulacra-production-b0d9.up.railway.app"
OUT = Path("/tmp/simulacra-e2e")
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


def wait_idle(
	token: str,
	tenant: str,
	pid: str,
	timeout: float = 300,
	*,
	expect_phase: str | None = None,
) -> dict:
	"""Wait until the project job is truly idle and (optionally) phase matches.

	Requires two consecutive stable polls so we don't race npm preview publish
	or in-memory job teardown.
	"""
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
			f"status={pstatus} source={(proj.get('prime') or {}).get('source')} "
			f"preview={bool(snap.get('preview_url'))}",
			flush=True,
		)
		building = pstatus in (
			"building_app",
			"publishing_preview",
			"extracting",
			"gating",
			"updating",
		) or (phase == "build" and pstatus not in ("ready", "failed", "draft"))
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
		time.sleep(3)
	note(False, "Timed out waiting for idle job")
	return last


def login() -> tuple[str, str]:
	email = "e2e-1786188418@cmul8.test"
	password = "E2eTestPass123!"
	code, sess = api("POST", "/auth/login", "", "default", {"email": email, "password": password})
	note(code == 200 and bool(sess.get("token")), "Login", f"status={code}")
	return sess["token"], sess["tenant_id"]


def main() -> int:
	OUT.mkdir(parents=True, exist_ok=True)
	token, tenant = login()
	# Always create a fresh project for full create→build→iterate proof
	existing = OUT / "pid.txt"
	if existing.exists():
		existing.unlink()

	snap = None
	for attempt in range(3):
		code, snap = api(
			"POST",
			"/projects",
			token,
			tenant,
			{
				"prompt": "A vendor risk dashboard from my diligence pack",
				"goal": "Vendor risk command center",
				"use_fixture": True,
			},
		)
		if code == 200 and snap.get("project"):
			break
		note(False, f"Create attempt {attempt+1}", f"status={code} detail={snap.get('detail')}")
		time.sleep(2)
	else:
		return 1
	pid = snap["project"]["id"]
	existing.write_text(pid)
	note(True, "Create project", pid)
	snap = wait_idle(token, tenant, pid, 180)

	proj = snap.get("project") or {}
	preview = snap.get("preview_url")
	chat = proj.get("chat") or []

	note(proj.get("phase") == "plan", "Scaffold leaves phase=plan", f"phase={proj.get('phase')} status={proj.get('status')}")
	note(bool(preview) and "127.0.0.1" not in str(preview), "Draft preview same-origin", str(preview))
	note(proj.get("gates_status") == "pass", "Gates pass", str(proj.get("gates_status")))
	note(any("How this works" in (m.get("content") or "") for m in chat), "Loop explained in plan chat")
	note((proj.get("prime") or {}).get("source") == "template", "Scaffold source=template", str((proj.get("prime") or {}).get("source")))

	# Preview assets
	if preview:
		path = preview if str(preview).startswith("/") else f"/projects/{pid}/preview/"
		req = urllib.request.Request(f"{BASE}{path}")
		try:
			with urllib.request.urlopen(req, timeout=30) as res:
				html = res.read().decode("utf-8", "replace")
			note(res.status == 200, "Preview index OK", f"bytes={len(html)}")
			# config.json
			cfg_url = f"{BASE}{path.rstrip('/')}/config.json"
			with urllib.request.urlopen(cfg_url, timeout=30) as res:
				cfg = json.loads(res.read().decode())
			note("title" in cfg, "Preview config.json", json.dumps(cfg)[:120])
		except Exception as e:
			note(False, "Preview assets", str(e))

	# Sources inventory (must not 404 behind SPA catch-all)
	code, sources = api("GET", f"/projects/{pid}/sources", token, tenant)
	note(code == 200, "GET /sources OK", f"status={code} detail={sources.get('detail')}")
	if code == 200:
		files = sources.get("files") or sources.get("sources") or []
		note(len(files) >= 1, "Sources list non-empty", f"n={len(files)}")

	# Plan chat
	code, _ = api("POST", f"/projects/{pid}/plan", token, tenant, {"message": "Keep dense ops dark aesthetic."})
	note(code == 200, "Plan chat accepted", f"status={code}")
	snap = wait_idle(token, tenant, pid, 120)
	proj = snap.get("project") or {}
	note(proj.get("phase") == "plan", "Plan chat keeps phase=plan", f"phase={proj.get('phase')}")

	# Build
	code, _ = api("POST", f"/projects/{pid}/approve", token, tenant, {})
	note(code in (200, 202), "Build app accepted", f"status={code}")
	snap = wait_idle(token, tenant, pid, 420, expect_phase="ready")
	proj = snap.get("project") or {}
	prime = proj.get("prime") or {}
	source = prime.get("source")
	note(proj.get("phase") == "ready", "After build phase=ready", f"phase={proj.get('phase')} status={proj.get('status')}")
	note(
		source in ("prime", "craft"),
		"Agent Built (source=prime|craft)",
		f"source={source} style_only={prime.get('style_only')} err={prime.get('last_error')}",
	)
	built_msg = any(
		m.get("role") == "assistant" and ("Built" in (m.get("content") or "") or m.get("source") == "prime")
		for m in (proj.get("chat") or [])
	)
	note(built_msg, "Build honesty message present")
	note(proj.get("status") == "ready", "After build status=ready", f"status={proj.get('status')}")

	# Iterate 1 — style / density
	n_before = len(proj.get("chat") or [])
	code, _ = api(
		"POST",
		f"/projects/{pid}/chat",
		token,
		tenant,
		{"message": "Make the KPI strip denser and improve contrast on theme labels."},
	)
	note(code in (200, 202), "Iterate chat accepted", f"status={code}")
	time.sleep(2)
	_, jobinfo = api("GET", f"/projects/{pid}/job", token, tenant)
	kind = (jobinfo.get("job") or {}).get("kind")
	note(kind == "iterate_run" or jobinfo.get("live"), "Iterate starts iterate_run", f"kind={kind} live={jobinfo.get('live')}")
	snap = wait_idle(token, tenant, pid, 420)
	proj = snap.get("project") or {}
	note(len(proj.get("chat") or []) > n_before, "Iterate adds chat turns")
	note(proj.get("status") == "ready", "After iterate status=ready", f"status={proj.get('status')}")
	last = [m for m in (proj.get("chat") or []) if m.get("role") == "assistant"][-1]
	silent_heuristic = last.get("source") == "heuristic" and "Updated the app layout" in (last.get("content") or "")
	note(not silent_heuristic, "No silent heuristic success on iterate", f"source={last.get('source')} {(last.get('content') or '')[:140]}")
	note(last.get("source") in ("prime", "heuristic", "error", "system", "craft"), "Iterate reply sourced", str(last.get("source")))

	# Iterate 2 — addition / layout change
	n_before = len(proj.get("chat") or [])
	code, _ = api(
		"POST",
		f"/projects/{pid}/chat",
		token,
		tenant,
		{
			"message": (
				"Add a Vendors section below the KPI strip listing vendor names from the data "
				"as a compact table with name and risk. Edit src/App.tsx."
			),
		},
	)
	note(code in (200, 202), "Addition iterate accepted", f"status={code}")
	snap = wait_idle(token, tenant, pid, 480)
	proj = snap.get("project") or {}
	note(len(proj.get("chat") or []) > n_before, "Addition iterate adds chat")
	last = [m for m in (proj.get("chat") or []) if m.get("role") == "assistant"][-1]
	note(
		last.get("source") in ("prime", "heuristic", "error", "system", "craft"),
		"Addition iterate reply sourced",
		f"source={last.get('source')} {(last.get('content') or '')[:160]}",
	)
	note(proj.get("phase") == "ready", "Still phase=ready after addition", f"phase={proj.get('phase')}")

	# Question-only
	code, _ = api(
		"POST",
		f"/projects/{pid}/chat",
		token,
		tenant,
		{"message": "How many high risk findings are there?"},
	)
	note(code == 200, "Question chat accepted", f"status={code}")
	time.sleep(2)
	_, jobinfo = api("GET", f"/projects/{pid}/job", token, tenant)
	note(not jobinfo.get("live"), "Question does not leave live builder job", f"live={jobinfo.get('live')} kind={(jobinfo.get('job') or {}).get('kind')}")
	snap = wait_idle(token, tenant, pid, 90)
	proj = snap.get("project") or {}
	last = [m for m in (proj.get("chat") or []) if m.get("role") == "assistant"][-1]
	note(bool(last.get("content")), "Question answered", f"source={last.get('source')} {(last.get('content') or '')[:120]}")
	note("high" in (last.get("content") or "").lower() or any(c.isdigit() for c in (last.get("content") or "")), "Question has a count", (last.get("content") or "")[:120])

	# Ship
	code, snap = api("POST", f"/projects/{pid}/deploy", token, tenant, {})
	note(code == 200, "Ship accepted", f"status={code} detail={snap.get('detail')}")
	_, snap = api("GET", f"/projects/{pid}", token, tenant)
	proj = snap.get("project") or {}
	note(proj.get("deployed") is True, "deployed flag set", str(proj.get("deployed")))
	note(any("Shipped" in (m.get("content") or "") for m in (proj.get("chat") or [])), "Ship receipt in chat")
	note(bool(snap.get("preview_url")) and "127.0.0.1" not in str(snap.get("preview_url")), "Ship URL same-origin", str(snap.get("preview_url")))

	# List
	code, listing = api("GET", "/projects", token, tenant)
	ids = [p.get("id") for p in (listing.get("projects") or [])]
	note(pid in ids, "Listed in home projects", f"n={len(ids)}")

	report = {
		"project_id": pid,
		"passes": passes,
		"faults": faults,
		"final_source": (proj.get("prime") or {}).get("source"),
		"deployed": proj.get("deployed"),
		"phase": proj.get("phase"),
		"status": proj.get("status"),
	}
	(OUT / "report.json").write_text(json.dumps(report, indent=2))
	print("\n=== SUMMARY ===", flush=True)
	print(f"project={pid} passes={len(passes)} faults={len(faults)}", flush=True)
	for f in faults:
		print(f" - {f}", flush=True)
	return 1 if faults else 0


if __name__ == "__main__":
	sys.exit(main())
