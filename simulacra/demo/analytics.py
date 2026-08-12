"""Pre-compute analytics for generated artifacts — schema-aware, not vendor-locked."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any


def _is_diligence(rows: list[dict[str, Any]]) -> bool:
	from .extract import is_diligence_rows

	return is_diligence_rows(rows)


def _categorical_breakdowns(rows: list[dict[str, Any]], *, limit_cols: int = 4) -> list[dict[str, Any]]:
	if not rows:
		return []
	cols = sorted({k for r in rows for k in r.keys()})
	skip = {"source_file", "evidence", "text", "body", "summary", "id"}
	out: list[dict[str, Any]] = []
	for col in cols:
		if col.lower() in skip:
			continue
		vals = [str(r.get(col) or "").strip() for r in rows if str(r.get(col) or "").strip()]
		uniq = set(vals)
		if len(vals) < 2 or len(uniq) < 2 or len(uniq) > max(24, len(rows) // 2 + 2):
			continue
		# Prefer short labels over long prose
		if sum(len(v) for v in uniq) / max(len(uniq), 1) > 48:
			continue
		counts = Counter(vals)
		out.append(
			{
				"field": col,
				"values": [
					{"label": lab, "count": c, "pct": round(100 * c / len(rows), 1)}
					for lab, c in counts.most_common(12)
				],
			}
		)
		if len(out) >= limit_cols:
			break
	return out


def build_analytics(rows: list[dict[str, Any]]) -> dict[str, Any]:
	now = datetime.now(UTC).isoformat()
	if not rows:
		return {
			"shape": "empty",
			"kpis": {"row_count": 0, "field_count": 0, "source_files": 0},
			"risk_distribution": [],
			"vendor_scores": [],
			"theme_breakdown": [],
			"field_breakdowns": [],
			"sources": [],
			"score_histogram": [],
			"generated_at": now,
		}

	cols = sorted({k for r in rows for k in r.keys()})
	sources: Counter[str] = Counter(str(r.get("source_file") or "unknown") for r in rows)
	base = {
		"shape": "diligence" if _is_diligence(rows) else "generic",
		"kpis": {
			"row_count": len(rows),
			"field_count": len(cols),
			"source_files": len([s for s in sources if s and s != "unknown"]),
			# Compat aliases used by older scaffolds
			"total_findings": len(rows),
		},
		"field_breakdowns": _categorical_breakdowns(rows),
		"sources": [{"file": f, "count": c} for f, c in sources.most_common()],
		"columns": cols,
		"generated_at": now,
		# Always present so templates don't crash
		"risk_distribution": [],
		"vendor_scores": [],
		"theme_breakdown": [],
		"score_histogram": [],
	}

	if not _is_diligence(rows):
		return base

	vendors: dict[str, list[dict]] = defaultdict(list)
	themes: Counter[str] = Counter()
	risk_counts: Counter[str] = Counter()
	scores: list[int] = []

	for r in rows:
		v = str(r.get("vendor", "Unknown"))
		vendors[v].append(r)
		themes[str(r.get("theme", "other"))] += 1
		rl = str(r.get("risk_level", "medium"))
		risk_counts[rl] += 1
		try:
			scores.append(int(r.get("risk_score", 0)))
		except (TypeError, ValueError):
			pass

	vendor_scores = []
	for name, vrows in vendors.items():
		vscores = [int(r.get("risk_score", 0)) for r in vrows]
		max_score = max(vscores) if vscores else 0
		levels = [str(r.get("risk_level", "")) for r in vrows]
		worst = "high" if "high" in levels else ("medium" if "medium" in levels else "low")
		vendor_scores.append(
			{
				"vendor": name,
				"findings": len(vrows),
				"max_score": max_score,
				"avg_score": round(sum(vscores) / len(vscores), 1) if vscores else 0,
				"risk_level": worst,
				"themes": list({str(r.get("theme", "")) for r in vrows})[:4],
			}
		)
	vendor_scores.sort(key=lambda x: x["max_score"], reverse=True)

	theme_breakdown = [
		{"theme": t, "count": c, "pct": round(100 * c / len(rows), 1)}
		for t, c in themes.most_common(12)
	]

	risk_distribution = [
		{
			"level": lvl,
			"count": risk_counts.get(lvl, 0),
			"pct": round(100 * risk_counts.get(lvl, 0) / len(rows), 1),
		}
		for lvl in ("high", "medium", "low")
	]

	buckets = [0, 0, 0, 0, 0]
	for s in scores:
		idx = min(4, s // 21)
		buckets[idx] += 1
	score_histogram = [
		{"range": "0–20", "count": buckets[0]},
		{"range": "21–40", "count": buckets[1]},
		{"range": "41–60", "count": buckets[2]},
		{"range": "61–80", "count": buckets[3]},
		{"range": "81–100", "count": buckets[4]},
	]

	high = risk_counts.get("high", 0)
	avg = round(sum(scores) / len(scores), 1) if scores else 0
	base["kpis"].update(
		{
			"unique_vendors": len(vendors),
			"high_risk": high,
			"medium_risk": risk_counts.get("medium", 0),
			"low_risk": risk_counts.get("low", 0),
			"avg_score": avg,
			"max_score": max(scores) if scores else 0,
			"critical_vendors": sum(1 for v in vendor_scores if v["risk_level"] == "high"),
			"vendors": len(vendors),
		}
	)
	base["risk_distribution"] = risk_distribution
	base["vendor_scores"] = vendor_scores
	base["theme_breakdown"] = theme_breakdown
	base["score_histogram"] = score_histogram
	return base
