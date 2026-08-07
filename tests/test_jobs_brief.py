"""Unit tests for design brief + job bounds (no Prime required)."""

from __future__ import annotations

import time

from simulacra.demo.design_brief import default_brief, merge_brief, merge_notes_from_message
from simulacra.demo.jobs import BOUNDS, JobCancelled, JobRecord, check_bounds, _jobs, _lock


def test_default_brief_has_required_keys():
	b = default_brief(prompt="Build a vendor risk board")
	assert b["aesthetic"]["direction"]
	assert b["aesthetic"]["chrome"] == "no-cards"
	assert "must_have" in b["information_architecture"]


def test_merge_brief_patches_nested():
	base = default_brief()
	out = merge_brief(base, {"aesthetic": {"density": "dense", "palette": {"accent": "#112233"}}})
	assert out["aesthetic"]["density"] == "dense"
	assert out["aesthetic"]["palette"]["accent"] == "#112233"
	assert out["aesthetic"]["direction"] == base["aesthetic"]["direction"]


def test_merge_notes_from_message():
	b = default_brief()
	out = merge_notes_from_message(b, "Make it denser and dark mode with #3D8B6E accent")
	assert out["aesthetic"]["density"] == "dense"
	assert out["aesthetic"]["color_mode"] == "dark"
	assert out["aesthetic"]["palette"]["accent"] == "#3D8B6E"


def test_check_bounds_timeout():
	job = JobRecord(
		id="job_test",
		project_id="proj_test",
		kind="build_run",
		deadline=time.monotonic() - 1,
		max_steps=40,
	)
	with _lock:
		_jobs["proj_test"] = job
	try:
		try:
			check_bounds("proj_test")
			assert False, "expected JobCancelled"
		except JobCancelled as exc:
			assert "timeout" in str(exc)
	finally:
		with _lock:
			_jobs.pop("proj_test", None)


def test_bounds_table_complete():
	for kind in ("plan_ask", "build_run", "iterate_run", "iterate_ask"):
		assert kind in BOUNDS
		assert BOUNDS[kind]["timeout"] > 0
		assert BOUNDS[kind]["max_steps"] > 0
