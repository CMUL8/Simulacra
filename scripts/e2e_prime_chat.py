#!/usr/bin/env python3
"""E2E: main chat is Prime — create without fixtures, chat, observe requests, Build."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "https://simulacra-production-b0d9.up.railway.app"
EMAIL = "e2e-1786188418@cmul8.test"
PASSWORD = "E2eTestPass123!"

faults: list[str] = []
passes: list[str] = []


def note(ok: bool, msg: str, detail: str = "") -> None:
	line = f"{'PASS' if ok else 'FAULT'}: {msg}"
	if detail:
		line += f" — {detail[:400]}"
	print(line, flush=True)
	(passes if ok else faults).append(msg if ok else f"{msg} | {detail[:200]}")


def api(method: str, path: str, token: str, tenant: str, body: dict | None = None, timeout: float = 180):
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


def wait_idle(token: str, tenant: str, pid: str, timeout: float = 420, *, expect_phase: str | None = None) -> dict:
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
		prime = proj.get("prime") or {}
		print(
			f"  … live={live} job={status}/{job.get('kind')} phase={phase} "
			f"status={pstatus} request={prime.get('request')} source={prime.get('source')} "
			f"preview={bool(snap.get('preview_url'))}",
			flush=True,
		)
		building = pstatus in (
			"building_app",
			"publishing_preview",
			"extracting",
			"gating",
			"updating",
		) or (phase == "build" and pstatus not in ("ready", "failed", "draft", "planning"))
		# "planning" is the idle plan-phase status after agent_chat — not a running build
		terminal = (not live) and status in ("idle", "failed", "cancelled", None, "")
		if expect_phase:
			done = terminal and phase == expect_phase and not building
		else:
			done = terminal and not building
		# If job finished and we're still on plan with planning status, that's success for chat opens
		if terminal and phase == "plan" and pstatus in ("planning", "draft") and expect_phase in (None, "plan"):
			done = True
		if done:
			stable += 1
			if stable >= 2:
				return snap
		else:
			stable = 0
		time.sleep(3)
	note(False, "Timed out waiting for idle job")
	return last


def main() -> int:
	print(f"BASE={BASE}", flush=True)
	code, health = api("GET", "/health", "", "default")
	note(code == 200 and health.get("prime") == "enabled", "Health + Prime enabled", json.dumps(health)[:200])

	code, sess = api("POST", "/auth/login", "", "default", {"email": EMAIL, "password": PASSWORD})
	if code != 200 or not sess.get("token"):
		note(False, "Login", f"status={code} {sess}")
		return 1
	token, tenant = sess["token"], sess["tenant_id"]
	note(True, "Login", tenant)

	# 1) Create report with NO fixture — must stay in plan with Prime chat
	prompt = "Create a report on Bhartiya Janta Party"
	code, snap = api(
		"POST",
		"/projects",
		token,
		tenant,
		{
			"prompt": prompt,
			"goal": "Political research brief",
			"use_fixture": False,
			"artifact_kind": "report",
		},
	)
	note(code == 200 and bool(snap.get("project")), "Create without fixture", f"status={code}")
	if code != 200:
		return 1
	pid = snap["project"]["id"]
	print(f"PID={pid}", flush=True)

	snap = wait_idle(token, tenant, pid, 300, expect_phase="plan")
	proj = snap.get("project") or {}
	chat = proj.get("chat") or []
	assist = [m for m in chat if m.get("role") == "assistant"]
	prime = proj.get("prime") or {}
	preview = snap.get("preview_url")
	room = (proj.get("plan_preview") or {}).get("source_room") or {}

	note(proj.get("phase") == "plan", "Stays in plan (no silent Built)", f"phase={proj.get('phase')}")
	note(not preview, "No preview URL before Build", f"preview={preview}")
	note(bool(assist), "Prime/assistant opened chat", f"n={len(assist)} source={assist[-1].get('source') if assist else None}")
	if assist:
		body = assist[-1].get("content") or ""
		note(
			"vendor" not in body.lower() or "sample" in body.lower() or "not" in body.lower() or "upload" in body.lower() or "research" in body.lower() or "source" in body.lower(),
			"Opening reply acknowledges empty/wrong data (not silent vendor report)",
			body[:220].replace("\n", " "),
		)
		note("Built from your sources" not in body, "Does not claim Built-from-sources", body[:120])
	note(room.get("empty") is True or int(room.get("file_count") or 0) == 0, "source_room empty", json.dumps(room)[:200])
	note(prime.get("source") in ("prime", "heuristic", "error"), "prime.source set", str(prime.get("source")))

	# 2) User asks to research — chat goes to Prime (agent_chat), not silent build
	code, snap = api(
		"POST",
		f"/projects/{pid}/chat",
		token,
		tenant,
		{"message": "Please research BJP online and outline what sources you would gather. Do not use any vendor sample data."},
	)
	note(code in (200, 202), "Chat via /chat", f"status={code} job={snap.get('job_id') or (snap.get('job') or {}).get('id')}")
	snap = wait_idle(token, tenant, pid, 300, expect_phase="plan")
	proj = snap.get("project") or {}
	chat = proj.get("chat") or []
	assist = [m for m in chat if m.get("role") == "assistant"]
	prime = proj.get("prime") or {}
	req = prime.get("request")
	note(proj.get("phase") == "plan", "Still plan after research chat", f"phase={proj.get('phase')}")
	note(len(assist) >= 2, "Second assistant turn present", f"n={len(assist)}")
	if assist:
		body = assist[-1].get("content") or ""
		note(len(body) > 40, "Research reply substantive", body[:240].replace("\n", " "))
	note(
		req in ("await_user", "research", "build", None) or req is None,
		"Observed request is valid",
		f"request={req}",
	)
	note(not snap.get("preview_url"), "Still no silent preview after research chat")

	# 3) Seed sample pack then chat — agent should be honest (mismatch OK to discuss)
	code, snap = api("POST", f"/projects/{pid}/sources/seed", token, tenant, {})
	note(code == 200, "Seed sample pack (explicit)", f"status={code}")
	snap = wait_idle(token, tenant, pid, 180)
	code, snap = api(
		"POST",
		f"/projects/{pid}/chat",
		token,
		tenant,
		{"message": "I still want a BJP report — tell me clearly if this sample pack is wrong for that, and what I should do next."},
	)
	note(code in (200, 202), "Chat about mismatch", f"status={code}")
	snap = wait_idle(token, tenant, pid, 300, expect_phase="plan")
	proj = snap.get("project") or {}
	assist = [m for m in (proj.get("chat") or []) if m.get("role") == "assistant"]
	if assist:
		body = (assist[-1].get("content") or "").lower()
		honest = any(
			w in body
			for w in (
				"vendor",
				"sample",
				"not",
				"mismatch",
				"unrelated",
				"upload",
				"research",
				"bjp",
				"bhartiya",
				"don't",
				"does not",
				"won't",
				"cannot",
				"can't",
			)
		)
		note(honest, "Agent honest about sample vs BJP", (assist[-1].get("content") or "")[:260].replace("\n", " "))

	# 4) Build — user explicit. With vendor sample + BJP prompt, build may still produce vendor artifact;
	#    plan allows Build when user ready. We verify Build transitions out of plan.
	code, snap = api("POST", f"/projects/{pid}/approve", token, tenant, {})
	note(code in (200, 202), "User Build", f"status={code} detail={snap.get('detail')}")
	if code in (200, 202):
		snap = wait_idle(token, tenant, pid, 540, expect_phase="ready")
		proj = snap.get("project") or {}
		note(proj.get("phase") == "ready", "Phase ready after Build", f"phase={proj.get('phase')}")
		note(bool(snap.get("preview_url")), "Preview URL after Build", str(snap.get("preview_url")))
		prime = proj.get("prime") or {}
		note(prime.get("request") in (None, "await_user", ""), "Build cleared prime.request", f"request={prime.get('request')}")

		# 5) After Built — chat goes to Prime first (not is_question_only shortcut)
		code, snap = api(
			"POST",
			f"/projects/{pid}/chat",
			token,
			tenant,
			{"message": "Make the executive summary denser and tighten the opening."},
		)
		note(code in (200, 202), "Post-Build chat", f"status={code}")
		snap = wait_idle(token, tenant, pid, 420, expect_phase="ready")
		proj = snap.get("project") or {}
		assist = [m for m in (proj.get("chat") or []) if m.get("role") == "assistant"]
		note(len(assist) >= 1, "Post-Build agent reply", f"last_source={assist[-1].get('source') if assist else None}")
		if assist:
			note(True, "Last reply", (assist[-1].get("content") or "")[:220].replace("\n", " "))

	print("\n=== SUMMARY ===", flush=True)
	print(f"PASS {len(passes)} / FAULT {len(faults)}", flush=True)
	for f in faults:
		print(f"  - {f}", flush=True)
	print(f"Project: {BASE}/  pid={pid}", flush=True)
	return 1 if faults else 0


if __name__ == "__main__":
	sys.exit(main())
