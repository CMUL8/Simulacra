#!/usr/bin/env python3
"""Multi-turn smoke: open + N follow-ups should stay on Prime (source=prime)."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://simulacra-production-b0d9.up.railway.app"
EMAIL = "e2e-1786188418@cmul8.test"
PASSWORD = "E2eTestPass123!"

# Typical user steers — enough to stress session resume / no stall-kill
FOLLOW_UPS = [
	"Outline what research sources you'd gather for this — no vendor sample data.",
	"Focus on recent national election performance and coalition partners.",
	"Keep the tone analytical, not campaign-y. Sections: exec summary, history, org, positions.",
	"What would you put in the evidence callouts if I upload news clips later?",
	"I'm not attaching the sample pack. Confirm you're not using vendor-risk rows.",
	"When I hit Build with an empty room, what will the first draft look like?",
	"Add a short glossary section to the plan.",
	"Make the title punchier — something like 'BJP: Power, Partners, and Policy'.",
	"Ok research later — for now prepare the scaffold path and ask me to Build when ready.",
	"Confirm current request state: await_user or build?",
	"One more: list the section outline in bullets.",
	"Thanks — remind me what to upload before Build.",
]


def api(method, path, token, tenant, body=None, timeout=180):
	data = None if body is None else json.dumps(body).encode()
	headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant, "Accept": "application/json"}
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
			payload = {"detail": raw[:400]}
		return e.code, payload
	except Exception as e:
		return 0, {"detail": str(e)}


def wait_idle(token, tenant, pid, timeout=480):
	deadline = time.time() + timeout
	stable = 0
	last = {}
	while time.time() < deadline:
		_, snap = api("GET", f"/projects/{pid}", token, tenant)
		_, jobinfo = api("GET", f"/projects/{pid}/job", token, tenant)
		last = snap
		job = jobinfo.get("job") or {}
		live = bool(jobinfo.get("live"))
		status = job.get("status") or "idle"
		proj = snap.get("project") or {}
		prime = proj.get("prime") or {}
		print(
			f"  … live={live} job={status}/{job.get('kind')} source={prime.get('source')} "
			f"request={prime.get('request')} err={str(prime.get('last_error') or '')[:60]}",
			flush=True,
		)
		if (not live) and status in ("idle", "failed", "cancelled", None, ""):
			stable += 1
			if stable >= 2:
				return snap
		else:
			stable = 0
		time.sleep(3)
	return last


def main() -> int:
	code, sess = api("POST", "/auth/login", "", "default", {"email": EMAIL, "password": PASSWORD})
	if code != 200:
		print("LOGIN FAIL", sess)
		return 1
	token, tenant = sess["token"], sess["tenant_id"]

	code, snap = api(
		"POST",
		"/projects",
		token,
		tenant,
		{
			"prompt": "Create a report on Bhartiya Janta Party",
			"use_fixture": False,
			"artifact_kind": "report",
		},
	)
	pid = snap["project"]["id"]
	print(f"PID={pid}", flush=True)
	snap = wait_idle(token, tenant, pid)
	proj = snap["project"]
	prime = proj.get("prime") or {}
	open_src = prime.get("source")
	print(f"OPEN source={open_src} subtitle={proj.get('app_config', {}).get('subtitle')}", flush=True)

	prime_ok = 1 if open_src == "prime" else 0
	heuristic = 0 if open_src == "prime" else 1
	errors = 0

	for i, msg in enumerate(FOLLOW_UPS, 1):
		print(f"\n--- follow-up {i}/{len(FOLLOW_UPS)} ---", flush=True)
		code, snap = api("POST", f"/projects/{pid}/chat", token, tenant, {"message": msg})
		if code not in (200, 202):
			print(f"CHAT FAIL {code} {snap}")
			errors += 1
			continue
		snap = wait_idle(token, tenant, pid)
		proj = snap["project"]
		prime = proj.get("prime") or {}
		src = prime.get("source")
		assist = [m for m in (proj.get("chat") or []) if m.get("role") == "assistant"]
		last = (assist[-1].get("content") or "")[:160].replace("\n", " ") if assist else ""
		print(f"source={src} request={prime.get('request')} reply={last}", flush=True)
		if src == "prime":
			prime_ok += 1
		elif src == "heuristic":
			heuristic += 1
		else:
			errors += 1

	total = 1 + len(FOLLOW_UPS)
	print("\n=== MULTI-TURN SUMMARY ===", flush=True)
	print(f"prime={prime_ok}/{total} heuristic={heuristic} other_err={errors} pid={pid}", flush=True)
	# Need at least 10/12 follow-ups on Prime (open + 10 of 12 follow-ups ≈ 11/13)
	# User asked ~10/12 follow-ups — require >= 10 prime turns among follow-ups OR >= 11 total including open
	follow_prime = prime_ok - (1 if open_src == "prime" else 0)
	ok = open_src == "prime" and follow_prime >= 10
	print(f"follow_ups_on_prime={follow_prime}/{len(FOLLOW_UPS)} pass={ok}", flush=True)
	return 0 if ok else 1


if __name__ == "__main__":
	sys.exit(main())
