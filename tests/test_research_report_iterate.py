"""Honesty + research bundle for report iterate preview."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulacra.demo import paths as paths_mod
from simulacra.demo import runs as runs_mod
from simulacra.demo.design_brief import default_brief
from simulacra.demo.prime_builder import _deterministic_layout_pass, prime_build_app
from simulacra.demo.research_bundle import (
	ensure_research_aware_report_app,
	parse_research_payload,
	write_research_bundle,
)
from simulacra.demo.runs import create_project, project_dir, save_state


@pytest.fixture()
def report_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
	runs = tmp_path / "runs"
	runs.mkdir()
	monkeypatch.setattr(paths_mod, "RUNS_DIR", runs)
	monkeypatch.setattr(runs_mod, "RUNS_DIR", runs)
	monkeypatch.delenv("SIMULACRA_USE_PRIME", raising=False)
	monkeypatch.setattr("simulacra.demo.prime_builder.prime_enabled", lambda: False)
	monkeypatch.setattr("simulacra.demo.prime_hook.prime_enabled", lambda: False)
	monkeypatch.setattr(
		"simulacra.demo.tenants.assert_under_project_quota",
		lambda *_a, **_k: None,
	)
	monkeypatch.setattr(
		"simulacra.demo.tenants.assert_tenant_active",
		lambda *_a, **_k: None,
	)
	state = create_project("Vendor risk diligence report", use_fixture=True)
	state.artifact_kind = "report"
	state.app_config.title = "Vendor Risk Command Center"
	state.app_config.subtitle = "Third-party diligence"
	root = project_dir(state.id)
	app = root / "app"
	src = app / "src"
	pub = app / "public"
	src.mkdir(parents=True, exist_ok=True)
	pub.mkdir(parents=True, exist_ok=True)

	template = Path(__file__).resolve().parents[1] / "templates" / "report" / "src"
	# Simulate an *old* vendor-only App (no research.json fetch)
	old_app = (template / "App.tsx").read_text().replace(
		'fetch(asset("research.json")).then((r) => (r.ok ? r.json() : null)).catch(() => null),',
		"",
	)
	# If template already has research, strip the research mode block marker check
	if "research.json" in old_app:
		# Force a vendor-only stub
		old_app = (
			'export default function App() {\n'
			'  return <article className="report"><h1>Vendor Risk</h1></article>;\n'
			"}\n"
		)
	(src / "App.tsx").write_text(old_app)
	(src / "styles.css").write_text((template / "styles.css").read_text())
	(pub / "config.json").write_text(
		json.dumps({"title": "Vendor Risk", "subtitle": "diligence", "artifactKind": "report"})
	)
	(pub / "data.json").write_text("[]")
	(pub / "analytics.json").write_text(json.dumps({"kpis": {}}))
	state.design_brief = default_brief(prompt=state.prompt)
	save_state(state)
	return state.id, app


def test_report_craft_marker_only_is_style_only(report_scaffold: tuple[str, Path]) -> None:
	"""Craft stamp without research must not claim layout_customized content win."""
	pid, app = report_scaffold
	meta = prime_build_app(app, "Polish the report", project_id=pid, row_count=0)
	assert meta["style_only"] is True
	assert meta["layout_customized"] is False
	assert meta["source"] == "style"
	# Marker may be present, but this is not an "## Updated" content win
	assert meta.get("ok") is True


def test_deterministic_layout_report_without_research_not_content_win(
	report_scaffold: tuple[str, Path],
) -> None:
	pid, app = report_scaffold
	assert _deterministic_layout_pass(app, pid) is False


def test_research_bundle_from_bjp_json(report_scaffold: tuple[str, Path]) -> None:
	pid, app = report_scaffold
	root = project_dir(pid)
	work = root / "work"
	work.mkdir(parents=True, exist_ok=True)
	sample = {
		"title": "BJP Political Landscape",
		"subtitle": "Briefing for strategy",
		"sections": [
			{
				"heading": "Origins",
				"body": "The Bharatiya Janata Party emerged from the Jana Sangh tradition.",
				"bullets": ["Founded 1980", "RSS institutional roots"],
			},
			{
				"heading": "Current posture",
				"body": "Governing majority with a strong organizational machine.",
				"bullets": ["National footprint", "Cadre density"],
			},
		],
	}
	(work / "bjp_research.json").write_text(json.dumps(sample), encoding="utf-8")

	bundle = write_research_bundle(pid, force=True, message="Replace with BJP research")
	assert bundle is not None
	assert bundle["title"] == "BJP Political Landscape"
	assert len(bundle["sections"]) == 2

	out = app / "public" / "research.json"
	assert out.is_file()
	loaded = json.loads(out.read_text())
	assert loaded["sections"][0]["heading"] == "Origins"

	# Mirrored into data-room for inventory
	room_copy = root / "inputs" / "data-room" / "bjp_research.json"
	assert room_copy.is_file()


def test_ensure_research_aware_rewrites_old_app(report_scaffold: tuple[str, Path]) -> None:
	pid, app = report_scaffold
	(app / "public" / "research.json").write_text(
		json.dumps(
			{
				"title": "BJP",
				"subtitle": "",
				"source_note": "test",
				"sections": [{"heading": "One", "body": "Body", "bullets": []}],
			}
		),
		encoding="utf-8",
	)
	assert "research.json" not in (app / "src" / "App.tsx").read_text()
	assert ensure_research_aware_report_app(app) is True
	tsx = (app / "src" / "App.tsx").read_text()
	assert "research.json" in tsx
	assert "sections" in tsx


def test_craft_with_research_is_layout_customized(report_scaffold: tuple[str, Path]) -> None:
	pid, app = report_scaffold
	(app / "public" / "research.json").write_text(
		json.dumps(
			{
				"title": "BJP Brief",
				"sections": [{"heading": "Overview", "body": "Notes", "bullets": ["a"]}],
			}
		),
		encoding="utf-8",
	)
	meta = prime_build_app(app, "Rebuild report from research", project_id=pid, row_count=0)
	assert meta["layout_customized"] is True
	assert meta["style_only"] is False
	assert meta["source"] == "prime"
	assert "research.json" in (app / "src" / "App.tsx").read_text()


def test_parse_research_payload_key_findings() -> None:
	raw = {
		"title": "Topic",
		"key_findings": ["Alpha", "Beta"],
		"overview": "A short overview of the topic.",
	}
	parsed = parse_research_payload(raw)
	assert parsed["title"] == "Topic"
	assert any(s["heading"] == "Overview" for s in parsed["sections"])
	assert any(s["heading"] == "Key points" for s in parsed["sections"])


def test_iterate_does_not_rename_report_to_vendor_risk(report_scaffold: tuple[str, Path]) -> None:
	from simulacra.demo.pipeline import _iterate_merge_app_config
	from simulacra.demo.runs import load_state

	pid, _app = report_scaffold
	state = load_state(pid)
	state.app_config.title = "BJP Landscape Brief"
	state.artifact_kind = "report"
	save_state(state)
	state = load_state(pid)
	_iterate_merge_app_config(state, "Replace vendor sample with researched BJP content")
	assert state.app_config.title == "BJP Landscape Brief"


def test_observe_promotes_agent_research_into_data_room(report_scaffold: tuple[str, Path]) -> None:
	from simulacra.demo.research_bundle import observe_and_promote_research, snapshot_research_mtimes
	from simulacra.demo.sources import data_room_dir, list_source_files

	pid, _app = report_scaffold
	root = project_dir(pid)
	work = root / "work"
	work.mkdir(parents=True, exist_ok=True)
	before = snapshot_research_mtimes(pid)
	payload = {
		"title": "BJP Research",
		"sections": [{"heading": "Overview", "body": "Founded 1980", "bullets": ["Modi"]}],
	}
	(work / "bjp_research.json").write_text(json.dumps(payload), encoding="utf-8")
	result = observe_and_promote_research(pid, before=before, force=False, artifact_kind="report")
	assert "bjp_research.json" in result["promoted"]
	assert (data_room_dir(pid) / "bjp_research.json").is_file()
	names = {s.name for s in list_source_files(pid)}
	assert "bjp_research.json" in names
	assert (root / "app" / "public" / "research.json").is_file()
