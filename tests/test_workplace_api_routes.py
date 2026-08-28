from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from fastapi import APIRouter, HTTPException

from apps.api import cmul8_routes, main as api_main, mission_routes, workplace_routes
from simulacra.workplace.config import WORKPLACE_FLAGS
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.demo.identity import AuthContext, User


def _context(user_id: str) -> AuthContext:
    return AuthContext(
        user=User(id=user_id, email=f"{user_id}@example.test", name=user_id, password_hash="unused"),
        tenant_id="tenant_api", role="member", auth_via="test",
    )


def test_workplace_aggregator_mounts_present_subrouters_and_skips_only_missing_target_module(monkeypatch):
    present = ModuleType("apps.api.workplace_summary_routes")
    present.router = APIRouter(prefix="/summary")
    present.router.add_api_route("/", lambda: {"ok": True}, methods=["GET"])
    imports: list[str] = []

    def load(name: str):
        imports.append(name)
        if name == workplace_routes.OPTIONAL_SUBROUTER_MODULES[0]:
            return present
        if name == workplace_routes.OPTIONAL_SUBROUTER_MODULES[1]:
            raise ModuleNotFoundError(name=name)
        if name == workplace_routes.OPTIONAL_SUBROUTER_MODULES[2]:
            raise ModuleNotFoundError(name="nested_dependency")
        raise ModuleNotFoundError(name=name)

    target = APIRouter()
    monkeypatch.setattr(workplace_routes.importlib, "import_module", load)
    with pytest.raises(ModuleNotFoundError) as nested:
        workplace_routes.register_if_present(target)
    assert nested.value.name == "nested_dependency"
    assert imports[:3] == list(workplace_routes.OPTIONAL_SUBROUTER_MODULES[:3])
    assert any(getattr(route, "original_router", None) is present.router for route in target.routes)


def test_workspace_routes_enforce_membership_and_bounded_public_fields(monkeypatch, tmp_path: Path):
    project_root = tmp_path / "project_api"; project_root.mkdir()
    monkeypatch.setattr(cmul8_routes, "_collaboration_root", tmp_path / "control")
    monkeypatch.setattr(cmul8_routes, "project_dir", lambda _project_id: project_root)
    monkeypatch.setattr(cmul8_routes, "load_state", lambda _project_id: SimpleNamespace(
        app_config=SimpleNamespace(title="Support"), goal="Resolve", prompt="",
    ))
    repository = JsonCollaborationRepository(cmul8_routes._collaboration_root)
    CollaborationService(repository).create_room(tenant_id="tenant_api", project_id="project_api", creator_id="owner")

    with pytest.raises(HTTPException) as denied:
        cmul8_routes.get_room("project_api", _context("outsider"))
    assert denied.value.status_code == 403

    view = cmul8_routes.get_room("project_api", _context("owner"))
    assert set(view["room"]) == {"id", "members", "revision", "created_at", "updated_at"}
    forbidden = {"tenant_id", "project_id", "provider", "runtime", "host", "path", "session", "invocation", "lease", "sandbox"}
    assert forbidden.isdisjoint(str(view).lower())
    bootstrap = api_main.auth_me(_context("owner"))
    assert bootstrap["workplace_flags"] == {name: False for name in WORKPLACE_FLAGS}
    assert set(bootstrap["workplace_flags"]) == set(WORKPLACE_FLAGS)


def test_public_error_envelope_excludes_banned_fields():
    raw = "provider runtime MCP worker host /private/run raw exception credential session invocation lease sandbox"
    mission_error = mission_routes._err(RuntimeError(raw))
    room_error = cmul8_routes._translate(RuntimeError(raw))
    for error in (mission_error, room_error):
        assert set(error.detail) <= {"code", "message"}
        envelope = json.dumps(error.detail).lower()
        assert all(
            value not in envelope
            for value in (
                "provider", "runtime", "mcp", "worker", "host", "path",
                "credential", "session", "invocation", "lease", "sandbox", "exception",
            )
        )
