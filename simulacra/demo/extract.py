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


def is_diligence_rows(rows: list[dict[str, Any]]) -> bool:
	"""True only when rows already carry a real vendor+risk schema (never invent it)."""
	if not rows:
		return False
	cols: set[str] = set()
	for r in rows[:80]:
		cols.update(str(k).lower() for k in r.keys())
	return "vendor" in cols and ("risk_score" in cols or "risk_level" in cols)


def _attach_source(row: dict[str, Any], source: str) -> dict[str, Any]:
	out = dict(row)
	if "source_file" not in out or not out.get("source_file"):
		out["source_file"] = source
	return out


def _parse_markdown_block(text: str, source: str) -> list[dict[str, Any]]:
	"""Topic-neutral markdown extract: headings + bullets/paragraphs. No forged risk schema."""
	rows: list[dict[str, Any]] = []
	heading = ""
	for line in text.splitlines():
		raw = line.rstrip()
		line = raw.strip()
		if not line:
			continue
		if re.match(r"^#+\s+", line):
			heading = re.sub(r"^#+\s+", "", line).strip()
			continue
		if line.startswith(("- ", "* ")):
			body = line[2:].strip()
			rows.append(
				_attach_source(
					{
						"heading": heading or Path(source).stem.replace("_", " "),
						"text": body,
					},
					source,
				)
			)
			continue
		# Keep short prose paragraphs under the current heading
		if heading and not line.startswith("#"):
			rows.append(
				_attach_source(
					{
						"heading": heading,
						"text": line,
					},
					source,
				)
			)
	if not rows and text.strip():
		rows.append(
			_attach_source(
				{
					"heading": Path(source).stem.replace("_", " ")[:80] or "document",
					"text": text.strip()[:4000],
				},
				source,
			)
		)
	return rows


def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
	seen: set[str] = set()
	unique: list[dict[str, Any]] = []
	for row in rows:
		key = json.dumps(row, sort_keys=True, default=str)[:500]
		if key in seen:
			continue
		seen.add(key)
		unique.append(row)
	return unique


def _json_items(payload: Any) -> list[Any]:
	if isinstance(payload, list):
		return payload
	if not isinstance(payload, dict):
		return []
	for key in ("rows", "data", "records", "items", "findings", "results"):
		val = payload.get(key)
		if isinstance(val, list):
			return val
	# Single object document
	return [payload]


def extract_data_room_report(
	data_room: Path, *, project_id: str | None = None
) -> ExtractReport:
	"""Extract with per-file status — one bad file does not kill the room.

	Native: .md/.txt/.csv/.json — preserve native columns; never invent vendor/risk.
	Optional Firecrawl (FIRECRAWL_API_KEY): .pdf/.docx/.xlsx/… → markdown → neutral rows
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
					ExtractFileReport(name=rel, status="empty", detail="No rows parsed")
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
						_attach_source(
							{
								"heading": Path(rel).stem.replace("_", " ")[:60] or "document",
								"text": md_text[:4000],
							},
							rel,
						)
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
				clean = {k: (v if v is not None else "") for k, v in row.items() if k}
				# Coerce obvious numeric fields when present (keep as string otherwise)
				for key in ("risk_score", "score", "seats", "year", "count", "value"):
					if key in clean and str(clean[key]).strip() != "":
						try:
							clean[key] = int(float(str(clean[key])))
						except (TypeError, ValueError):
							pass
				rows.append(_attach_source(clean, rel))
		return rows
	if name.endswith(".json"):
		try:
			payload = json.loads(path.read_text(encoding="utf-8"))
		except json.JSONDecodeError as exc:
			raise ValueError(f"Invalid JSON: {exc}") from exc
		items = _json_items(payload)
		rows = []
		for item in items:
			if not isinstance(item, dict):
				rows.append(_attach_source({"text": str(item)}, rel))
				continue
			rows.append(_attach_source(item, rel))
		return rows
	return []


def extract_data_room(data_room: Path, *, project_id: str | None = None) -> list[dict[str, Any]]:
	"""Backward-compatible: return rows only."""
	return extract_data_room_report(data_room, project_id=project_id).rows


def write_summary(rows: list[dict[str, Any]], prompt: str) -> str:
	cols = sorted({k for r in rows for k in r.keys()})
	sources = sorted({str(r.get("source_file") or "") for r in rows if r.get("source_file")})
	lines = [
		"# Research summary",
		"",
		f"**Task:** {prompt}",
		"",
		f"**Rows:** {len(rows)}",
		f"**Fields:** {', '.join(cols[:24]) or 'none'}",
		f"**Sources:** {', '.join(sources[:12]) or 'none'}",
	]
	if is_diligence_rows(rows):
		vendors = sorted({str(r.get("vendor") or "") for r in rows if r.get("vendor")})
		high = sum(1 for r in rows if str(r.get("risk_level")) == "high")
		lines.extend(
			[
				"",
				f"**Note:** Room includes vendor/risk columns ({len(vendors)} vendors, {high} high).",
			]
		)
	return "\n".join(lines) + "\n"
