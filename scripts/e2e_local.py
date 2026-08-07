#!/usr/bin/env python3
"""End-to-end local test: plan → build → deploy → verify data app."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000"
PROMPT = "A vendor risk dashboard from diligence files ranked by severity"


def api(method: str, path: str, body: dict | None = None) -> dict:
	req = urllib.request.Request(
		f"{API}{path}",
		data=json.dumps(body).encode() if body else None,
		headers={"Content-Type": "application/json"} if body else {},
		method=method,
	)
	with urllib.request.urlopen(req, timeout=300) as res:
		return json.loads(res.read())


def fetch_url(url: str) -> str:
	with urllib.request.urlopen(url, timeout=30) as res:
		return res.read().decode()


def main() -> int:
	print("=" * 60)
	print("Simulacra E2E — local deploy test")
	print("=" * 60)

	# Health
	health = api("GET", "/health")
	print(f"\n✓ API health: {health}")

	# Create (plan)
	print("\n[1] Create project (plan phase)…")
	snap = api("POST", "/projects", {"prompt": PROMPT, "goal": "Track vendor risk for ops team", "use_fixture": True})
	pid = snap["project"]["id"]
	print(f"    project: {pid}")
	print(f"    phase: {snap['project']['phase']}")
	print(f"    title: {snap['project']['app_config']['title']}")
	pp = snap["project"].get("plan_preview") or {}
	print(f"    plan preview: {pp.get('row_count', 0)} rows, {pp.get('high_risk', 0)} high risk")
	print(f"    vendors: {', '.join((pp.get('vendors') or [])[:5])}…")

	# Plan chat
	print("\n[2] Plan chat…")
	snap = api("POST", f"/projects/{pid}/plan", {"message": "How many high-risk vendors are there?"})
	print(f"    chat messages: {len(snap['project']['chat'])}")

	# Approve & build (async 202 — poll until ready)
	print("\n[3] Approve & build…")
	snap = api("POST", f"/projects/{pid}/approve")
	print(f"    job: {snap.get('job_id') or (snap.get('job') or {}).get('id')}")
	import time

	for _ in range(120):
		snap = api("GET", f"/projects/{pid}")
		status = snap["project"]["status"]
		phase = snap["project"]["phase"]
		job_status = (snap.get("job") or snap["project"].get("job") or {}).get("status")
		if phase == "ready" or status in ("ready", "failed", "deployed"):
			break
		if job_status in ("idle", "failed", "cancelled") and phase == "ready":
			break
		time.sleep(1.5)
	project = snap["project"]
	preview_url = snap.get("preview_url")
	print(f"    status: {project['status']}")
	print(f"    gates: {project['gates_status']}")
	print(f"    rows: {project['row_count']}")
	print(f"    prime.source: {(project.get('prime') or {}).get('source')}")
	print(f"    preview: {preview_url}")
	if project["status"] == "failed":
		print("    ✗ build failed")
		return 1

	# Events
	events = api("GET", f"/projects/{pid}/events").get("events", [])
	print(f"    trace events: {len(events)}")

	# API preview data
	print("\n[4] API preview data (DuckDB)…")
	pd = snap.get("preview_data", {})
	cols = pd.get("columns", [])
	rows = pd.get("rows", [])
	print(f"    columns: {cols}")
	print(f"    row_count: {pd.get('row_count', 0)}")
	if rows:
		top = rows[0]
		print(f"    top row: vendor={top.get('vendor')}, risk={top.get('risk_level')}, score={top.get('risk_score')}")

	# Deploy
	print("\n[5] Deploy…")
	snap = api("POST", f"/projects/{pid}/deploy")
	project = snap["project"]
	print(f"    deployed: {project['deployed']}")
	print(f"    status: {project['status']}")

	# Built app artifacts
	print("\n[6] Built app artifacts…")
	from pathlib import Path

	root = Path(__file__).resolve().parents[1]
	app_dir = root / "runs" / pid / "app"
	data_path = app_dir / "public" / "data.json"
	cfg_path = app_dir / "public" / "config.json"
	if not data_path.exists():
		print(f"    ✗ missing {data_path}")
		return 1
	app_data = json.loads(data_path.read_text())
	app_cfg = json.loads(cfg_path.read_text())
	analytics_path = app_dir / "public" / "analytics.json"
	if not analytics_path.exists():
		print(f"    ✗ missing analytics.json")
		return 1
	analytics = json.loads(analytics_path.read_text())
	print(f"    config.title: {app_cfg.get('title')}")
	print(f"    config.layout: {app_cfg.get('layout')}")
	print(f"    analytics vendors: {analytics.get('kpis', {}).get('unique_vendors')}")
	print(f"    analytics high risk: {analytics.get('kpis', {}).get('high_risk')}")
	print(f"    config.sortColumn: {app_cfg.get('sortColumn')} ({app_cfg.get('sortDirection')})")
	print(f"    data.json rows: {len(app_data)}")
	high = sum(1 for r in app_data if r.get("risk_level") == "high")
	print(f"    high risk in app: {high}")

	# Sort check — app sorts client-side by risk_score desc
	scores = [r.get("risk_score", 0) for r in app_data]
	display_scores = sorted(scores, reverse=True)
	sorted_ok = display_scores == sorted(display_scores, reverse=True)
	print(f"    client sort (risk_score desc): {sorted_ok}")
	sorted_rows = sorted(app_data, key=lambda r: r.get("risk_score", 0), reverse=True)
	if sorted_rows:
		print(f"    highest: {sorted_rows[0].get('vendor')} ({sorted_rows[0].get('risk_score')})")
		print(f"    lowest:  {sorted_rows[-1].get('vendor')} ({sorted_rows[-1].get('risk_score')})")

	# Live preview HTTP
	print("\n[7] Live preview HTTP…")
	if not preview_url:
		print("    ✗ no preview URL")
		return 1
	try:
		html = fetch_url(preview_url)
		print(f"    preview HTML: {len(html)} bytes (SPA — title loaded via JS)")
		live_data = json.loads(fetch_url(f"{preview_url.rstrip('/')}/data.json"))
		live_cfg = json.loads(fetch_url(f"{preview_url.rstrip('/')}/config.json"))
		print(f"    live data.json rows: {len(live_data)}")
		print(f"    live config title: {live_cfg.get('title')}")
	except urllib.error.URLError as exc:
		print(f"    ✗ preview unreachable: {exc}")
		return 1

	# Gates audit
	print("\n[8] Gates audit…")
	audit = api("GET", f"/projects/{pid}/audit")
	gates = audit.get("gates", {})
	print(f"    gates status: {gates.get('status')}")
	for g in gates.get("results", []):
		icon = "✓" if g.get("passed") else "✗"
		print(f"    {icon} {g.get('gate')}: {g.get('detail')}")

	# Assertions
	print("\n" + "=" * 60)
	errors: list[str] = []
	if project["status"] != "deployed":
		errors.append(f"expected status deployed, got {project['status']}")
	if project["gates_status"] != "pass":
		errors.append(f"gates failed: {project['gates_status']}")
	if project["row_count"] < 10:
		errors.append(f"expected 10+ rows from enriched fixtures, got {project['row_count']}")
	if len(app_data) != project["row_count"]:
		errors.append(f"app data rows {len(app_data)} != project row_count {project['row_count']}")
	if not sorted_ok:
		errors.append("app data not sorted by risk_score desc")
	if app_cfg.get("layout") != "command_center":
		errors.append("expected command_center layout")

	if errors:
		print("FAILED")
		for e in errors:
			print(f"  ✗ {e}")
		return 1

	print("PASSED")
	print(f"\n  Open console: http://localhost:5173")
	print(f"  Open data app: {preview_url}")
	print("=" * 60)
	return 0


if __name__ == "__main__":
	raise SystemExit(main())
