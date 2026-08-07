"""SIEM export + optional webhook forwarder for audit events.

Formats: json (NDJSON), cef (ArcSight CEF), hec (Splunk HEC event envelope).
Forwarding: SIMULACRA_SIEM_WEBHOOK (+ optional SIMULACRA_SIEM_TOKEN, SIMULACRA_SIEM_FORMAT).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("simulacra.siem")

SEVERITY = {
	"ok": 3,
	"warn": 6,
	"error": 8,
	"fail": 8,
}


def siem_format() -> str:
	return (os.environ.get("SIMULACRA_SIEM_FORMAT") or "json").lower()


def siem_webhook() -> str | None:
	url = (os.environ.get("SIMULACRA_SIEM_WEBHOOK") or "").strip()
	return url or None


def siem_status() -> dict[str, Any]:
	return {
		"webhook_configured": bool(siem_webhook()),
		"format": siem_format(),
		"token_set": bool(os.environ.get("SIMULACRA_SIEM_TOKEN")),
	}


def to_cef(evt: dict[str, Any]) -> str:
	"""Common Event Format (ArcSight-compatible)."""
	sev = SEVERITY.get(str(evt.get("status") or "ok"), 3)
	action = str(evt.get("action") or "unknown").replace("|", "_")
	tenant = str(evt.get("tenant_id") or "-")
	user = str(evt.get("user_id") or "-")
	resource = str(evt.get("resource") or "-")
	detail = json.dumps(evt.get("detail") or {}, default=str)
	# CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|Name|Severity|Extension
	ext = (
		f"rt={evt.get('ts', '')} "
		f"suser={_cef_escape(user)} "
		f"cs1={_cef_escape(tenant)} cs1Label=tenantId "
		f"cs2={_cef_escape(resource)} cs2Label=resource "
		f"msg={_cef_escape(detail[:500])} "
		f"externalId={_cef_escape(str(evt.get('id') or ''))}"
	)
	return f"CEF:0|CMUL8|Simulacra|0.6|{action}|{action}|{sev}|{ext}"


def _cef_escape(value: str) -> str:
	return (
		value.replace("\\", "\\\\")
		.replace("=", "\\=")
		.replace("\n", "\\n")
		.replace("\r", "\\r")
	)


def to_hec(evt: dict[str, Any]) -> dict[str, Any]:
	"""Splunk HTTP Event Collector envelope."""
	return {
		"time": evt.get("ts"),
		"source": "simulacra",
		"sourcetype": "simulacra:audit",
		"event": evt,
	}


def format_events(events: list[dict[str, Any]], fmt: str | None = None) -> str:
	fmt = (fmt or siem_format()).lower()
	if fmt == "cef":
		return "\n".join(to_cef(e) for e in events) + ("\n" if events else "")
	if fmt in ("hec", "splunk"):
		return "\n".join(json.dumps(to_hec(e), default=str) for e in events) + (
			"\n" if events else ""
		)
	# ndjson
	return "\n".join(json.dumps(e, default=str) for e in events) + ("\n" if events else "")


def forward_event(evt: dict[str, Any]) -> dict[str, Any]:
	"""POST a single event to the configured SIEM webhook. Never raises."""
	url = siem_webhook()
	if not url:
		return {"forwarded": False, "reason": "no_webhook"}
	fmt = siem_format()
	try:
		if fmt == "cef":
			body = to_cef(evt).encode()
			content_type = "text/plain"
		elif fmt in ("hec", "splunk"):
			body = json.dumps(to_hec(evt), default=str).encode()
			content_type = "application/json"
		else:
			body = json.dumps(evt, default=str).encode()
			content_type = "application/json"
		headers = {"Content-Type": content_type, "User-Agent": "Simulacra-SIEM/0.6"}
		token = os.environ.get("SIMULACRA_SIEM_TOKEN")
		if token:
			headers["Authorization"] = f"Bearer {token}"
			# Splunk HEC often wants Splunk <token>
			if fmt in ("hec", "splunk"):
				headers["Authorization"] = f"Splunk {token}"
		req = urllib.request.Request(url, data=body, headers=headers, method="POST")
		with urllib.request.urlopen(req, timeout=10) as resp:
			return {"forwarded": True, "status": resp.status, "format": fmt}
	except Exception as exc:  # noqa: BLE001
		log.warning("siem forward failed: %s", exc)
		return {"forwarded": False, "error": str(exc)[:200], "format": fmt}


def forward_event_async(evt: dict[str, Any]) -> None:
	threading.Thread(target=forward_event, args=(evt,), daemon=True, name="siem-forward").start()


def export_bundle(
	events: list[dict[str, Any]],
	*,
	fmt: str | None = None,
	flush: bool = False,
) -> dict[str, Any]:
	"""Build export payload; optionally flush each event to webhook."""
	fmt = (fmt or siem_format()).lower()
	body = format_events(events, fmt)
	result: dict[str, Any] = {
		"format": fmt,
		"count": len(events),
		"body": body,
		"content_type": "text/plain" if fmt == "cef" else "application/x-ndjson",
	}
	if flush and siem_webhook():
		ok = 0
		for e in events:
			if forward_event(e).get("forwarded"):
				ok += 1
		result["flushed"] = ok
	return result


def download_filename(fmt: str | None = None) -> str:
	fmt = (fmt or siem_format()).lower()
	ext = {"cef": "cef", "hec": "ndjson", "splunk": "ndjson"}.get(fmt, "ndjson")
	return f"simulacra-audit.{ext}"
