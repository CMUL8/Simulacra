"""Builder craft fallback — App.tsx must change when agent writes nothing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulacra.demo import paths as paths_mod
from simulacra.demo import runs as runs_mod
from simulacra.demo.design_brief import default_brief
from simulacra.demo.prime_builder import (
	_app_tsx_hash,
	_deterministic_layout_pass,
	prime_build_app,
)
from simulacra.demo.runs import create_project, project_dir, save_state


@pytest.fixture()
def app_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, Path]:
	runs = tmp_path / "runs"
	runs.mkdir()
	monkeypatch.setattr(paths_mod, "RUNS_DIR", runs)
	monkeypatch.setattr(runs_mod, "RUNS_DIR", runs)
	monkeypatch.delenv("SIMULACRA_USE_PRIME", raising=False)
	monkeypatch.setattr(
		"simulacra.demo.tenants.assert_under_project_quota",
		lambda *_a, **_k: None,
	)
	monkeypatch.setattr(
		"simulacra.demo.tenants.assert_tenant_active",
		lambda *_a, **_k: None,
	)
	state = create_project("Vendor risk command center for ops", use_fixture=True)
	root = project_dir(state.id)
	app = root / "app"
	src = app / "src"
	pub = app / "public"
	src.mkdir(parents=True, exist_ok=True)
	pub.mkdir(parents=True, exist_ok=True)

	template = Path(__file__).resolve().parents[1] / "templates" / "internal-app" / "src"
	(src / "App.tsx").write_text((template / "App.tsx").read_text())
	(src / "styles.css").write_text((template / "styles.css").read_text())
	(pub / "config.json").write_text(
		json.dumps({"title": "Draft", "subtitle": "tmp", "searchEnabled": True})
	)
	state.design_brief = default_brief(prompt=state.prompt)
	state.design_brief["aesthetic"]["density"] = "dense"
	state.design_brief["aesthetic"]["chrome"] = "no-cards"
	state.design_brief["aesthetic"]["direction"] = "utilitarian"
	save_state(state)
	return state.id, app


def test_deterministic_layout_changes_app_tsx(app_scaffold: tuple[str, Path]) -> None:
	pid, app = app_scaffold
	before = _app_tsx_hash(app)
	assert _deterministic_layout_pass(app, pid)
	after = _app_tsx_hash(app)
	assert after != before
	tsx = (app / "src" / "App.tsx").read_text()
	assert "simulacra_craft:" in tsx
	assert "density-dense" in tsx
	assert "chrome-no-cards" in tsx
	assert "kpi-row-priority" in tsx


def test_prime_build_app_offline_uses_craft(app_scaffold: tuple[str, Path]) -> None:
	pid, app = app_scaffold
	before = _app_tsx_hash(app)
	meta = prime_build_app(app, "Build vendor risk app", project_id=pid, row_count=10)
	assert meta["layout_customized"] is True
	assert meta["style_only"] is False
	assert meta["source"] == "craft"
	assert meta["ok"] is True
	assert _app_tsx_hash(app) != before
