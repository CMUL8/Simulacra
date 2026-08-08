from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from .sources import (
	EXTRACTABLE_EXT,
	ExtractFileReport,
	ExtractReport,
	KNOWN_UNSUPPORTED,
)

RISK_WORDS = {
	"high": ("critical", "severe", "breach", "sanction", "fraud", "high risk"),
	"medium": ("delay", "concern", "review", "medium", "watch"),
	"low": ("stable", "low risk", "minor", "acceptable"),
}


def _risk_level(text: str) -> tuple[str, int]:
	lower = text.lower()
	score = 50
	level = "medium"
	for lvl, words in RISK_WORDS.items():
		for w in words:
			if w in lower:
				if lvl == "high":
					score = max(score, 85)
					level = "high"
				elif lvl == "medium" and level != "high":
					score = max(score, 55)
					level = "medium"
				elif lvl == "low" and level == "medium":
					score = min(score, 35)
					level = "low"
	return level, score


def _parse_markdown_block(text: str, source: str) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	vendor = None
	for line in text.splitlines():
		line = line.strip()
		if re.match(r"^#+\s+", line):
			vendor = re.sub(r"^#+\s+", "", line).strip()
			continue
		if not vendor or not line or line.startswith("#"):
			continue
		if line.startswith("- "):
			body = line[2:].strip()
			level, score = _risk_level(body)
			rows.append(
				{
					"vendor": vendor,
					"theme": body.split(":")[0] if ":" in body else "finding",
					"risk_level": level,
					"risk_score": score,
					"evidence": body,
					"source_file": source,
					"region": "",
					"owner": "",
				}
			)
	return rows


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	seen: set[str] = set()
	unique: list[dict[str, Any]] = []
	for row in rows:
		key = f"{row.get('vendor')}|{row.get('evidence')}"
		if key in seen:
			continue
		seen.add(key)
		unique.append(row)
	return unique


def extract_data_room_report(
	data_room: Path, *, project_id: str | None = None
) -> ExtractReport:
	"""Extract with per-file status — one bad file does not kill the room.

	Native: .md/.txt/.csv/.json
	Optional Firecrawl (FIRECRAWL_API_KEY): .pdf/.docx/.xlsx/… → markdown → findings
	"""
	from .firecrawl import can_firecrawl_parse, firecrawl_enabled, parse_and_cache, parse_document_to_markdown

	report = ExtractReport()
	if not data_room.exists():
		report.errors.append("Data room directory missing")
		return report

	all_rows: list[dict[str, Any]] = []
	for path in sorted(data_room.rglob("*")):
		if not path.is_file():
			continue
		rel = str(path.relative_to(data_room))
		ext = path.suffix.lower()

		if ext in EXTRACTABLE_EXT:
			try:
				file_rows = _extract_one(path, rel)
			except Exception as exc:  # noqa: BLE001
				msg = f"{rel}: {exc}"[:240]
				report.files.append(ExtractFileReport(name=rel, status="error", detail=str(exc)[:200]))
				report.errors.append(msg)
				continue
			if not file_rows:
				report.files.append(
					ExtractFileReport(name=rel, status="empty", detail="No findings parsed")
				)
			else:
				report.files.append(
					ExtractFileReport(name=rel, status="ok", rows=len(file_rows), detail="ok")
				)
				all_rows.extend(file_rows)
			continue

		if can_firecrawl_parse(path):
			try:
				md_text = None
				if project_id:
					md_path = parse_and_cache(project_id, path, rel)
					if md_path:
						md_text = md_path.read_text(encoding="utf-8")
				if not md_text:
					md_text = parse_document_to_markdown(path)
				if not md_text:
					raise ValueError("Firecrawl returned no markdown")
				file_rows = _parse_markdown_block(md_text, rel)
				if not file_rows:
					file_rows = [
						{
							"vendor": Path(rel).stem.replace("_", " ")[:60] or "document",
							"theme": "document",
							"risk_level": "medium",
							"risk_score": 50,
							"evidence": md_text[:2000],
							"source_file": rel,
							"region": "",
							"owner": "",
						}
					]
				report.files.append(
					ExtractFileReport(
						name=rel,
						status="ok",
						rows=len(file_rows),
						detail="parsed via Firecrawl",
					)
				)
				all_rows.extend(file_rows)
			except Exception as exc:  # noqa: BLE001
				msg = f"{rel}: Firecrawl parse failed — {exc}"[:240]
				report.files.append(
					ExtractFileReport(name=rel, status="error", detail=str(exc)[:200])
				)
				report.errors.append(msg)
			continue

		detail = f"unsupported type {ext or '(none)'}"
		if ext in KNOWN_UNSUPPORTED:
			detail = (
				f"{ext} needs FIRECRAWL_API_KEY (Firecrawl /parse)"
				if not firecrawl_enabled()
				else f"{ext} not extractable"
			)
		report.files.append(ExtractFileReport(name=rel, status="skipped", detail=detail))
		report.skipped.append(rel)

	report.rows = _dedupe(all_rows)
	return report


def _extract_one(path: Path, rel: str) -> list[dict[str, Any]]:
	name = path.name.lower()
	if name.endswith((".md", ".txt")):
		return _parse_markdown_block(path.read_text(encoding="utf-8", errors="replace"), rel)
	if name.endswith(".csv"):
		rows: list[dict[str, Any]] = []
		with path.open(encoding="utf-8", errors="replace") as f:
			reader = csv.DictReader(f)
			if not reader.fieldnames:
				return []
			for row in reader:
				vendor = row.get("vendor") or row.get("Vendor") or "unknown"
				theme = row.get("theme") or row.get("Theme") or row.get("finding") or "finding"
				evidence = row.get("evidence") or row.get("Evidence") or json.dumps(row)
				level, score = _risk_level(evidence)
				try:
					risk_score = int(row.get("risk_score") or score)
				except (TypeError, ValueError):
					risk_score = score
				rows.append(
					{
						"vendor": vendor,
						"theme": theme,
						"risk_level": row.get("risk_level") or level,
						"risk_score": risk_score,
						"evidence": evidence,
						"source_file": rel,
						"region": row.get("region") or row.get("Region") or "",
						"owner": row.get("owner") or row.get("Owner") or "",
					}
				)
		return rows
	if name.endswith(".json"):
		try:
			payload = json.loads(path.read_text(encoding="utf-8"))
		except json.JSONDecodeError as exc:
			raise ValueError(f"Invalid JSON: {exc}") from exc
		items = payload if isinstance(payload, list) else (payload.get("findings") or [])
		if not isinstance(items, list):
			raise ValueError("JSON must be a list or {findings: [...]}")
		rows = []
		for item in items:
			if not isinstance(item, dict):
				continue
			evidence = item.get("evidence") or item.get("summary") or str(item)
			level, score = _risk_level(str(evidence))
			try:
				risk_score = int(item.get("risk_score") or score)
			except (TypeError, ValueError):
				risk_score = score
			rows.append(
				{
					"vendor": item.get("vendor", "unknown"),
					"theme": item.get("theme", "finding"),
					"risk_level": item.get("risk_level") or level,
					"risk_score": risk_score,
					"evidence": evidence,
					"source_file": rel,
					"region": item.get("region", ""),
					"owner": item.get("owner", ""),
				}
			)
		return rows
	return []


def extract_data_room(data_room: Path, *, project_id: str | None = None) -> list[dict[str, Any]]:
	"""Backward-compatible: return rows only."""
	return extract_data_room_report(data_room, project_id=project_id).rows


def write_summary(rows: list[dict[str, Any]], prompt: str) -> str:
	vendors = sorted({r["vendor"] for r in rows})
	high = sum(1 for r in rows if r["risk_level"] == "high")
	return (
		f"# Research summary\n\n"
		f"**Task:** {prompt}\n\n"
		f"**Vendors:** {', '.join(vendors) or 'none'}\n\n"
		f"**Findings:** {len(rows)} ({high} high risk)\n"
	)
