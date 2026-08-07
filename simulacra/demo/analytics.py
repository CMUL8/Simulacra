"""Pre-compute dashboard analytics for generated apps."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any


def build_analytics(rows: list[dict[str, Any]]) -> dict[str, Any]:
	if not rows:
		return {
			"kpis": {},
			"risk_distribution": [],
			"vendor_scores": [],
			"theme_breakdown": [],
			"sources": [],
			"score_histogram": [],
		}

	vendors: dict[str, list[dict]] = defaultdict(list)
	themes: Counter[str] = Counter()
	sources: Counter[str] = Counter()
	risk_counts: Counter[str] = Counter()
	scores: list[int] = []

	for r in rows:
		v = str(r.get("vendor", "Unknown"))
		vendors[v].append(r)
		themes[str(r.get("theme", "other"))] += 1
		sources[str(r.get("source_file", "unknown"))] += 1
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
		{"level": lvl, "count": risk_counts.get(lvl, 0), "pct": round(100 * risk_counts.get(lvl, 0) / len(rows), 1)}
		for lvl in ("high", "medium", "low")
	]

	# Score buckets for histogram
	buckets = [0, 0, 0, 0, 0]  # 0-20, 21-40, 41-60, 61-80, 81-100
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

	return {
		"kpis": {
			"total_findings": len(rows),
			"unique_vendors": len(vendors),
			"high_risk": high,
			"medium_risk": risk_counts.get("medium", 0),
			"low_risk": risk_counts.get("low", 0),
			"avg_score": avg,
			"max_score": max(scores) if scores else 0,
			"source_files": len(sources),
			"critical_vendors": sum(1 for v in vendor_scores if v["risk_level"] == "high"),
		},
		"risk_distribution": risk_distribution,
		"vendor_scores": vendor_scores,
		"theme_breakdown": theme_breakdown,
		"sources": [{"file": f, "count": c} for f, c in sources.most_common()],
		"score_histogram": score_histogram,
		"generated_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
	}
