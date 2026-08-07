from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

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


def extract_data_room(data_room: Path) -> list[dict[str, Any]]:
	rows: list[dict[str, Any]] = []
	if not data_room.exists():
		return rows

	for path in sorted(data_room.rglob("*")):
		if not path.is_file():
			continue
		name = path.name.lower()
		rel = str(path.relative_to(data_room))
		if name.endswith((".md", ".txt")):
			rows.extend(_parse_markdown_block(path.read_text(encoding="utf-8", errors="replace"), rel))
		elif name.endswith(".csv"):
			with path.open(encoding="utf-8", errors="replace") as f:
				for row in csv.DictReader(f):
					vendor = row.get("vendor") or row.get("Vendor") or "unknown"
					theme = row.get("theme") or row.get("Theme") or row.get("finding") or "finding"
					evidence = row.get("evidence") or row.get("Evidence") or json.dumps(row)
					level, score = _risk_level(evidence)
					rows.append(
						{
							"vendor": vendor,
							"theme": theme,
							"risk_level": row.get("risk_level") or level,
							"risk_score": int(row.get("risk_score") or score),
							"evidence": evidence,
							"source_file": rel,
							"region": row.get("region") or row.get("Region") or "",
							"owner": row.get("owner") or row.get("Owner") or "",
						}
					)
		elif name.endswith(".json"):
			try:
				payload = json.loads(path.read_text(encoding="utf-8"))
			except json.JSONDecodeError:
				continue
			items = payload if isinstance(payload, list) else payload.get("findings", [])
			for item in items:
				if not isinstance(item, dict):
					continue
				evidence = item.get("evidence") or item.get("summary") or str(item)
				level, score = _risk_level(evidence)
				rows.append(
					{
						"vendor": item.get("vendor", "unknown"),
						"theme": item.get("theme", "finding"),
						"risk_level": item.get("risk_level") or level,
						"risk_score": int(item.get("risk_score") or score),
						"evidence": evidence,
						"source_file": rel,
						"region": item.get("region", ""),
						"owner": item.get("owner", ""),
					}
				)

	# dedupe rough duplicates
	seen: set[str] = set()
	unique: list[dict[str, Any]] = []
	for row in rows:
		key = f"{row['vendor']}|{row['evidence']}"
		if key in seen:
			continue
		seen.add(key)
		unique.append(row)
	return unique


def write_summary(rows: list[dict[str, Any]], prompt: str) -> str:
	vendors = sorted({r["vendor"] for r in rows})
	high = sum(1 for r in rows if r["risk_level"] == "high")
	return (
		f"# Research summary\n\n"
		f"**Task:** {prompt}\n\n"
		f"**Vendors:** {', '.join(vendors) or 'none'}\n\n"
		f"**Findings:** {len(rows)} ({high} high risk)\n"
	)
