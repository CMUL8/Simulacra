"""Dedicated preview-origin security contract tests."""
from __future__ import annotations

import hashlib
import asyncio
import multiprocessing
import threading
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from apps.api import preview_routes
from apps.api import main as api_main
from simulacra.demo.identity import AuthContext, User
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.models import Member
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.missions.models import Deliverable


def _request(*, host: str, origin: str | None = None, cookies: str | None = None, preflight: bool = False) -> Request:
    headers: list[tuple[bytes, bytes]] = [(b"host", host.encode())]
    if origin is not None:
        headers.append((b"origin", origin.encode()))
    if cookies is not None:
        headers.append((b"cookie", cookies.encode()))
    if preflight:
        headers.extend([(b"access-control-request-method", b"POST"), (b"access-control-request-headers", b"content-type")])
    return Request({"type": "http", "scheme": "https", "server": (host, 443), "path": "/", "method": "GET", "headers": headers})


def _context(actor_id: str = "human_1") -> AuthContext:
    return AuthContext(
        user=User(id=actor_id, email=f"{actor_id}@example.test", name="Human", password_hash="unused"),
        tenant_id="tenant_1", role="member", auth_via="test",
    )


@pytest.fixture
def configured_preview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", "true")
    monkeypatch.setenv("CONTROL_ORIGIN", "https://app.example.test")
    monkeypatch.setenv("PREVIEW_ORIGIN", "https://preview.example.test")
    monkeypatch.setenv("PREVIEW_REGISTRABLE_DOMAIN", "example.test")
    monkeypatch.setenv("CMUL8_PREVIEW_EXCHANGE_SECRET", "a test-only exchange signing secret")
    monkeypatch.setattr(preview_routes, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(preview_routes, "_exchange_root", tmp_path / ".workplace-control" / "preview-exchanges")
    repository = JsonCollaborationRepository(tmp_path / ".cmul8-control")
    CollaborationService(repository).create_room(
        tenant_id="tenant_1", project_id="project_1", creator_id="human_1",
    )
    monkeypatch.setattr(preview_routes, "_rooms_root", repository.root)
    workspace = tmp_path / "project"
    (workspace / "app" / "dist").mkdir(parents=True)
    (workspace / "app" / "dist" / "index.html").write_text("<main>approved</main>", encoding="utf-8")
    (workspace / "app" / "dist" / "nested.js").write_text("export default 'approved'", encoding="utf-8")
    manifest = {
        "index.html": hashlib.sha256((workspace / "app" / "dist" / "index.html").read_bytes()).hexdigest(),
        "nested.js": hashlib.sha256((workspace / "app" / "dist" / "nested.js").read_bytes()).hexdigest(),
    }
    monkeypatch.setattr(preview_routes, "project_dir", lambda _project_id: workspace)
    monkeypatch.setattr(
        preview_routes, "_promoted_manifest_from_snapshot",
        lambda tenant_id, project_id, _room, _visible: ("a" * 64, manifest)
        if (tenant_id, project_id) == ("tenant_1", "project_1") else None,
    )
    return workspace


def _exchange() -> tuple[str, str]:
    response = preview_routes.create_exchange(
        "project_1", preview_routes._ExchangeBody(), _request(host="app.example.test"), _context(),
    )
    return response["exchange_id"], response["exchange_proof"]


def _consume_in_separate_process(exchange_id: str, proof: str, results) -> None:
    try:
        response = preview_routes.consume_exchange(
            preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
            _request(host="preview.example.test", origin="https://app.example.test"),
        )
        results.put(response.status_code)
    except HTTPException as exc:
        results.put(exc.status_code)


def _add_preview_member(repository: JsonCollaborationRepository, actor_id: str, *, complete: bool) -> None:
    transaction_id = f"invite_accept_{actor_id}"
    room = repository.get_room("tenant_1", "project_1")
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id=actor_id, role="reviewer", display_name=f"{actor_id} private",
            transaction_id=transaction_id,
            visibility_state="committed" if complete else "pending_commit",
        )],
        revision=room.revision + 1,
    ), room.revision)
    if complete:
        journal = repository.root / ".invitation-acceptance" / "tenant_1" / "project_1" / f"{transaction_id}.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"project_id":"project_1","state":"COMPLETE","tenant_id":"tenant_1",'
            f'"transaction_id":"{transaction_id}"}}', encoding="utf-8",
        )


def test_preview_membership_is_complete_gated_for_exchange_capability_and_verification(monkeypatch, tmp_path: Path):
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    CollaborationService(repository).create_room(
        tenant_id="tenant_1", project_id="project_1", creator_id="legacy_human",
    )
    _add_preview_member(repository, "pending_human", complete=False)
    _add_preview_member(repository, "complete_human", complete=True)
    monkeypatch.setattr(preview_routes, "_rooms_root", repository.root)

    assert preview_routes._is_current_member("tenant_1", "project_1", "legacy_human") is True
    assert preview_routes._is_current_member("tenant_1", "project_1", "complete_human") is True
    assert preview_routes._is_current_member("tenant_1", "project_1", "pending_human") is False

    room = repository.get_room("tenant_1", "project_1")
    assert preview_routes._screened_human_verifier(room, "legacy_human") is True
    assert preview_routes._screened_human_verifier(room, "complete_human") is True
    assert preview_routes._screened_human_verifier(room, "pending_human") is False


def test_preview_visibility_precheck_never_holds_room_lock(monkeypatch, tmp_path: Path):
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    CollaborationService(repository).create_room(
        tenant_id="tenant_1", project_id="project_1", creator_id="legacy_human",
    )
    monkeypatch.setattr(preview_routes, "_rooms_root", repository.root)
    entered, release = threading.Event(), threading.Event()
    original = JsonCollaborationRepository.visible_member
    blocked = False

    def gated(self, room, actor_id):
        nonlocal blocked
        if actor_id == "legacy_human" and not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return original(self, room, actor_id)

    monkeypatch.setattr(JsonCollaborationRepository, "visible_member", gated)
    with ThreadPoolExecutor(max_workers=2) as pool:
        authorization = pool.submit(
            preview_routes._is_current_member, "tenant_1", "project_1", "legacy_human",
        )
        assert entered.wait(timeout=5)
        with repository.room_lock("tenant_1", "project_1") as room:
            assert room.project_id == "project_1"
        release.set()
        assert authorization.result(timeout=5) is True


def test_pending_invite_cannot_obtain_exchange_or_reuse_preview_capability(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", "true")
    monkeypatch.setenv("CONTROL_ORIGIN", "https://app.example.test")
    monkeypatch.setenv("PREVIEW_ORIGIN", "https://preview.example.test")
    monkeypatch.setenv("PREVIEW_REGISTRABLE_DOMAIN", "example.test")
    monkeypatch.setenv("CMUL8_PREVIEW_EXCHANGE_SECRET", "a test-only exchange signing secret")
    monkeypatch.setattr(preview_routes, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(preview_routes, "_exchange_root", tmp_path / ".workplace-control" / "preview-exchanges")
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    CollaborationService(repository).create_room(
        tenant_id="tenant_1", project_id="project_1", creator_id="legacy_human",
    )
    _add_preview_member(repository, "pending_human", complete=False)
    _add_preview_member(repository, "complete_human", complete=True)
    monkeypatch.setattr(preview_routes, "_rooms_root", repository.root)
    workspace = tmp_path / "project"
    (workspace / "app" / "dist").mkdir(parents=True)
    content = b"<main>approved</main>"
    (workspace / "app" / "dist" / "index.html").write_bytes(content)
    manifest = {"index.html": hashlib.sha256(content).hexdigest()}
    monkeypatch.setattr(preview_routes, "project_dir", lambda _project: workspace)
    monkeypatch.setattr(preview_routes, "_promoted_manifest_from_snapshot", lambda *_args: ("a" * 64, manifest))

    legacy = preview_routes.create_exchange(
        "project_1", preview_routes._ExchangeBody(), _request(host="app.example.test"),
        _context("legacy_human"),
    )
    assert legacy["exchange_id"]
    with pytest.raises(HTTPException) as pending_issue:
        preview_routes.create_exchange(
            "project_1", preview_routes._ExchangeBody(), _request(host="app.example.test"),
            _context("pending_human"),
        )
    assert pending_issue.value.status_code == 404

    issued = preview_routes.create_exchange(
        "project_1", preview_routes._ExchangeBody(), _request(host="app.example.test"),
        _context("complete_human"),
    )
    room = repository.get_room("tenant_1", "project_1")
    repository.save_room(replace(
        room,
        members=[replace(member, visibility_state="pending_commit") if member.actor_id == "complete_human" else member for member in room.members],
        revision=room.revision + 1,
    ), room.revision)
    with pytest.raises(HTTPException) as pending_consume:
        preview_routes.consume_exchange(
            preview_routes._ConsumeExchangeBody(
                exchange_id=issued["exchange_id"], exchange_proof=issued["exchange_proof"],
            ),
            _request(host="preview.example.test", origin="https://app.example.test"),
        )
    assert pending_consume.value.status_code == 404

    room = repository.get_room("tenant_1", "project_1")
    repository.save_room(replace(
        room,
        members=[replace(member, visibility_state="committed") if member.actor_id == "complete_human" else member for member in room.members],
        revision=room.revision + 1,
    ), room.revision)
    consumed = preview_routes.consume_exchange(
        preview_routes._ConsumeExchangeBody(
            exchange_id=issued["exchange_id"], exchange_proof=issued["exchange_proof"],
        ),
        _request(host="preview.example.test", origin="https://app.example.test"),
    )
    cookie = consumed.headers["set-cookie"].split(";", 1)[0]
    room = repository.get_room("tenant_1", "project_1")
    repository.save_room(replace(
        room,
        members=[replace(member, visibility_state="pending_commit") if member.actor_id == "complete_human" else member for member in room.members],
        revision=room.revision + 1,
    ), room.revision)
    with pytest.raises(HTTPException) as pending_reuse:
        preview_routes.serve_preview(
            "project_1", _request(host="preview.example.test", cookies=cookie), "index.html",
        )
    assert pending_reuse.value.status_code == 404


def _remove_preview_member(repository: JsonCollaborationRepository, finished: threading.Event) -> None:
    room = repository.get_room("tenant_1", "project_1")
    repository.save_room(replace(room, members=[], revision=room.revision + 1), room.revision)
    finished.set()


def test_preview_exchange_publication_finishes_before_concurrent_removal(configured_preview, monkeypatch):
    repository = JsonCollaborationRepository(preview_routes._rooms_root)
    entered, release, removal_finished = threading.Event(), threading.Event(), threading.Event()
    original = preview_routes._write_exchange_state

    def blocked_write(directory, state):
        entered.set()
        assert release.wait(timeout=5)
        return original(directory, state)

    monkeypatch.setattr(preview_routes, "_write_exchange_state", blocked_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(_exchange)
        assert entered.wait(timeout=5)
        removal = pool.submit(_remove_preview_member, repository, removal_finished)
        assert not removal_finished.wait(timeout=0.1)
        release.set()
        exchange_id, proof = publication.result(timeout=5)
        removal.result(timeout=5)
    assert exchange_id and proof and removal_finished.is_set()


def test_preview_cookie_publication_finishes_before_concurrent_removal(configured_preview, monkeypatch):
    repository = JsonCollaborationRepository(preview_routes._rooms_root)
    exchange_id, proof = _exchange()
    entered, release, removal_finished = threading.Event(), threading.Event(), threading.Event()
    original = preview_routes._write_exchange_state

    def blocked_write(directory, state):
        entered.set()
        assert release.wait(timeout=5)
        return original(directory, state)

    monkeypatch.setattr(preview_routes, "_write_exchange_state", blocked_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(
            preview_routes.consume_exchange,
            preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
            _request(host="preview.example.test", origin="https://app.example.test"),
        )
        assert entered.wait(timeout=5)
        removal = pool.submit(_remove_preview_member, repository, removal_finished)
        assert not removal_finished.wait(timeout=0.1)
        release.set()
        response = publication.result(timeout=5)
        removal.result(timeout=5)
    assert "mission_preview_" in response.headers["set-cookie"] and removal_finished.is_set()


def test_preview_byte_publication_finishes_before_concurrent_removal(configured_preview, monkeypatch):
    repository = JsonCollaborationRepository(preview_routes._rooms_root)
    exchange_id, proof = _exchange()
    consumed = preview_routes.consume_exchange(
        preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
        _request(host="preview.example.test", origin="https://app.example.test"),
    )
    cookie = consumed.headers["set-cookie"].split(";", 1)[0]
    entered, release, removal_finished = threading.Event(), threading.Event(), threading.Event()
    original = preview_routes._read_preview_file

    def blocked_read(workspace, relative):
        entered.set()
        assert release.wait(timeout=5)
        return original(workspace, relative)

    monkeypatch.setattr(preview_routes, "_read_preview_file", blocked_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(
            preview_routes.serve_preview, "project_1",
            _request(host="preview.example.test", cookies=cookie), "index.html",
        )
        assert entered.wait(timeout=5)
        removal = pool.submit(_remove_preview_member, repository, removal_finished)
        assert not removal_finished.wait(timeout=0.1)
        release.set()
        response = publication.result(timeout=5)
        removal.result(timeout=5)
    assert response.body == b"<main>approved</main>" and removal_finished.is_set()


def test_preview_exchange_requires_current_member_promoted_revision_and_exact_origin(configured_preview, monkeypatch):
    exchange_id, proof = _exchange()
    response = preview_routes.consume_exchange(
        preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
        _request(host="preview.example.test", origin="https://app.example.test"),
    )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "https://app.example.test"
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=none" in cookie
    assert "Domain=" not in cookie
    with pytest.raises(HTTPException) as replay:
        preview_routes.consume_exchange(
            preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
            _request(host="preview.example.test", origin="https://app.example.test"),
        )
    assert replay.value.status_code == 404
    monkeypatch.setattr(
        preview_routes, "_room_visibility_snapshot",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("removed")),
    )
    with pytest.raises(HTTPException):
        _exchange()


def test_preview_origin_preflight_allows_only_configured_control_origin(configured_preview):
    response = preview_routes.preview_exchange_preflight(_request(host="preview.example.test", origin="https://app.example.test", preflight=True))
    assert response.status_code == 204
    assert {key: response.headers[key] for key in (
        "access-control-allow-origin", "access-control-allow-credentials", "vary",
        "access-control-allow-methods", "access-control-allow-headers", "access-control-max-age",
    )} == {
        "access-control-allow-origin": "https://app.example.test",
        "access-control-allow-credentials": "true",
        "vary": "Origin",
        "access-control-allow-methods": "POST, OPTIONS",
        "access-control-allow-headers": "content-type",
        "access-control-max-age": "300",
    }
    with pytest.raises(HTTPException):
        preview_routes.preview_exchange_preflight(_request(host="preview.example.test", origin="https://attacker.example.test", preflight=True))


def test_preview_origin_exchange_cors_credentials_and_one_time_consumption(configured_preview):
    exchange_id, proof = _exchange()
    with pytest.raises(HTTPException):
        preview_routes.consume_exchange(
            preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
            _request(host="preview.example.test", origin="null"),
        )
    # A rejected origin cannot consume the proof.
    assert preview_routes.consume_exchange(
        preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
        _request(host="preview.example.test", origin="https://app.example.test"),
    ).status_code == 204


def test_preview_origin_denies_expired_revoked_cross_tenant_staging_and_guess_id(configured_preview, monkeypatch):
    exchange_id, proof = _exchange()
    response = preview_routes.consume_exchange(
        preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
        _request(host="preview.example.test", origin="https://app.example.test"),
    )
    cookie = response.headers["set-cookie"].split(";", 1)[0]
    asset = preview_routes.serve_preview("project_1", _request(host="preview.example.test", cookies=cookie), "nested.js")
    assert asset.body == b"export default 'approved'"
    assert asset.headers["cache-control"] == "private, no-store"
    assert asset.headers["x-content-type-options"] == "nosniff"
    assert asset.headers["content-security-policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; form-action 'self'; "
        "base-uri 'none'; object-src 'none'; frame-ancestors https://app.example.test"
    )
    with pytest.raises(HTTPException):
        preview_routes.serve_preview("project_1", _request(host="preview.example.test", cookies=cookie), "staging/secret.js")
    with pytest.raises(HTTPException):
        preview_routes.serve_preview("project_2", _request(host="preview.example.test", cookies=cookie), "index.html")
    monkeypatch.setattr(
        preview_routes, "_room_visibility_snapshot",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("removed")),
    )
    with pytest.raises(HTTPException):
        preview_routes.serve_preview("project_1", _request(host="preview.example.test", cookies=cookie), "index.html")


def test_preview_origin_requires_same_site_distinct_hostname_and_cookie_compatibility(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", "true")
    monkeypatch.setenv("CONTROL_ORIGIN", "https://app.example.test")
    monkeypatch.setenv("PREVIEW_ORIGIN", "https://app.example.test")
    monkeypatch.setenv("PREVIEW_REGISTRABLE_DOMAIN", "example.test")
    monkeypatch.setenv("CMUL8_PREVIEW_EXCHANGE_SECRET", "test-secret")
    assert preview_routes.preview_origin_config() is None
    monkeypatch.setenv("PREVIEW_ORIGIN", "https://preview.other.test")
    assert preview_routes.preview_origin_config() is None


@pytest.mark.parametrize("control,preview", [
    ("http://app.example.test", "http://preview.example.test"),
    ("https://app.example.test", "http://preview.example.test"),
])
def test_preview_origin_requires_same_site_https(monkeypatch: pytest.MonkeyPatch, control: str, preview: str):
    monkeypatch.setenv("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", "true")
    monkeypatch.setenv("CONTROL_ORIGIN", control)
    monkeypatch.setenv("PREVIEW_ORIGIN", preview)
    monkeypatch.setenv("PREVIEW_REGISTRABLE_DOMAIN", "example.test")
    monkeypatch.setenv("CMUL8_PREVIEW_EXCHANGE_SECRET", "test-secret")
    assert preview_routes.preview_origin_config() is None


def test_corrupt_exchange_ledger_fails_closed(configured_preview):
    _exchange()
    state = preview_routes._exchange_root / "state.json"
    state.write_text("not json", encoding="utf-8")
    with pytest.raises(HTTPException) as unavailable:
        _exchange()
    assert unavailable.value.status_code == 404


def test_exchange_consumption_is_atomic(configured_preview):
    exchange_id, proof = _exchange()

    def consume() -> int:
        try:
            return preview_routes.consume_exchange(
                preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
                _request(host="preview.example.test", origin="https://app.example.test"),
            ).status_code
        except HTTPException as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _item: consume(), range(2)))
    assert statuses == [204, 404]


def test_exchange_consumption_is_atomic_across_processes(configured_preview):
    exchange_id, proof = _exchange()
    context = multiprocessing.get_context("fork")
    results = context.Queue()
    processes = [context.Process(target=_consume_in_separate_process, args=(exchange_id, proof, results)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0
    assert sorted(results.get(timeout=2) for _ in processes) == [204, 404]


def test_preview_host_cannot_answer_control_apis_and_legacy_public_preview_is_gone(configured_preview):
    denied = asyncio.run(api_main._isolate_preview_origin(
        _request(host="preview.example.test"),
        lambda _request: _response(200),
    ))
    assert denied.status_code == 404
    assert not hasattr(api_main, "serve_project_preview")


@pytest.mark.parametrize("flag,secret,method,path,allowed", [
    ("false", None, "GET", "/health", False),
    ("false", None, "GET", "/", False),
    ("false", None, "GET", "/assets/main.js", False),
    ("false", None, "POST", "/preview/exchange", True),
    ("false", None, "OPTIONS", "/preview/exchange", True),
    ("false", None, "GET", "/preview/exchange", False),
    ("false", None, "GET", "/projects/project_1/preview", True),
    ("false", None, "POST", "/projects/project_1/preview", False),
    ("false", None, "GET", "/projects/../preview", False),
    ("true", None, "GET", "/docs", False),
])
def test_preview_host_isolated_even_when_preview_is_not_ready(monkeypatch, flag, secret, method, path, allowed):
    monkeypatch.setenv("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", flag)
    monkeypatch.setenv("PREVIEW_ORIGIN", "https://preview.example.test")
    monkeypatch.delenv("CONTROL_ORIGIN", raising=False)
    if secret is None:
        monkeypatch.delenv("CMUL8_PREVIEW_EXCHANGE_SECRET", raising=False)
    captured = []

    async def next_handler(_request):
        captured.append(True)
        return preview_routes.Response(status_code=200)

    request = _request(host="preview.example.test")
    request.scope["method"] = method
    request.scope["path"] = path
    response = asyncio.run(api_main._isolate_preview_origin(request, next_handler))
    assert (response.status_code == 200) is allowed
    assert bool(captured) is allowed


@pytest.mark.parametrize("origin", [
    "https://preview.example.test/not-an-origin",
    "https://preview.example.test:invalid-port",
    "https://human@preview.example.test",
    "preview.example.test",
])
def test_malformed_preview_origin_still_quarantines_its_safely_identifiable_host(monkeypatch, origin):
    """A bad deployment may not turn the preview hostname into a control host."""
    monkeypatch.setenv("PREVIEW_ORIGIN", origin)
    monkeypatch.delenv("CONTROL_ORIGIN", raising=False)
    monkeypatch.delenv("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", raising=False)
    monkeypatch.delenv("CMUL8_PREVIEW_EXCHANGE_SECRET", raising=False)
    assert preview_routes.preview_origin_config() is None
    assert preview_routes.preview_origin_hostname() == "preview.example.test"
    with pytest.raises(HTTPException) as unavailable:
        preview_routes.preview_exchange_preflight(
            _request(host="preview.example.test", origin="https://app.example.test", preflight=True)
        )
    assert unavailable.value.status_code == 404

    allowed_paths = {
        ("OPTIONS", "/preview/exchange"),
        ("POST", "/preview/exchange"),
        ("GET", "/projects/project_1/preview"),
    }
    async def next_handler(_request):
        return preview_routes.Response(status_code=200)

    for method, path in [
        ("GET", "/"), ("GET", "/health"), ("GET", "/docs"), ("GET", "/assets/main.js"),
        ("OPTIONS", "/preview/exchange"), ("POST", "/preview/exchange"), ("GET", "/projects/project_1/preview"),
    ]:
        request = _request(host="preview.example.test")
        request.scope["method"] = method
        request.scope["path"] = path
        response = asyncio.run(api_main._isolate_preview_origin(request, next_handler))
        assert (response.status_code == 200) is ((method, path) in allowed_paths)


def test_control_host_cannot_consume_or_serve_preview(configured_preview):
    exchange_id, proof = _exchange()
    with pytest.raises(HTTPException) as consume_denied:
        preview_routes.consume_exchange(
            preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
            _request(host="app.example.test", origin="https://app.example.test"),
        )
    assert consume_denied.value.status_code == 404
    with pytest.raises(HTTPException) as asset_denied:
        preview_routes.serve_preview("project_1", _request(host="app.example.test"), "index.html")
    assert asset_denied.value.status_code == 404


def test_ledger_malformed_row_and_symlink_fail_closed(configured_preview):
    _exchange()
    state_path = preview_routes._exchange_root / "state.json"
    state_path.write_text(json.dumps({"exchange": {"tenant_id": "tenant_1"}}), encoding="utf-8")
    with pytest.raises(HTTPException):
        _exchange()
    state_path.unlink()
    state_path.symlink_to(configured_preview / "outside.json")
    with pytest.raises(HTTPException):
        _exchange()


def test_exchange_ledger_ancestor_symlink_fails_closed(configured_preview, monkeypatch, tmp_path: Path):
    isolated = tmp_path / "isolated-runs"
    isolated.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (isolated / ".workplace-control").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(preview_routes, "RUNS_DIR", isolated)
    monkeypatch.setattr(preview_routes, "_exchange_root", isolated / ".workplace-control" / "preview-exchanges")
    with pytest.raises(HTTPException):
        _exchange()


def test_exact_expiry_boundary_is_unavailable(configured_preview, monkeypatch):
    monkeypatch.setattr(preview_routes, "_now", lambda: 100)
    exchange_id, proof = _exchange()
    ledger = preview_routes._exchange_root / "state.json"
    state = json.loads(ledger.read_text(encoding="utf-8"))
    state[exchange_id]["expires_at"] = 100
    ledger.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(HTTPException):
        preview_routes.consume_exchange(
            preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
            _request(host="preview.example.test", origin="https://app.example.test"),
        )


def test_capability_exact_expiry_boundary_is_unavailable(configured_preview, monkeypatch):
    monkeypatch.setattr(preview_routes, "_now", lambda: 100)
    exchange_id, proof = _exchange()
    response = preview_routes.consume_exchange(
        preview_routes._ConsumeExchangeBody(exchange_id=exchange_id, exchange_proof=proof),
        _request(host="preview.example.test", origin="https://app.example.test"),
    )
    cookie = response.headers["set-cookie"].split(";", 1)[0]
    monkeypatch.setattr(preview_routes, "_now", lambda: 400)
    with pytest.raises(HTTPException):
        preview_routes.serve_preview("project_1", _request(host="preview.example.test", cookies=cookie), "index.html")


@pytest.fixture
def real_promoted_manifest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    workspace = tmp_path / "project"
    dist = workspace / "app" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("approved index", encoding="utf-8")
    monkeypatch.setattr(preview_routes, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(preview_routes, "_rooms_root", tmp_path / ".cmul8-control")
    monkeypatch.setattr(preview_routes, "project_dir", lambda _project_id: workspace)
    repository = JsonMissionRepository(tmp_path / ".mission-control")
    return workspace, repository


def _write_manifest_deliverable(repository: JsonMissionRepository, *, evidence: dict, artifact_ref: str, digest: str, state: str = "verified", verified_hash: str | None = None, version: int = 1):
    item = Deliverable(
        id="deliverable_preview", tenant_id="tenant_1", project_id="project_1", mission_id="mission_1",
        type="code", name="Preview app", producer_id="agent_1", version=version, content_hash=digest,
        source_ref="mission/agent", artifact_ref=artifact_ref, validation_evidence=[evidence], state=state,
        verified_hash=verified_hash if verified_hash is not None else digest,
    )
    def mutate(records):
        records["mission"] = {"id": "mission_1", "tenant_id": "tenant_1", "project_id": "project_1"}
        records["deliverables"][item.id] = item.to_dict()
    repository.mutate("tenant_1", "project_1", mutate)


@pytest.mark.parametrize("evidence,artifact_ref,state,verified_hash", [
    ({"intended_target": "app/dist/index.html", "staged_artifact_ref": "staging/index.html"}, "other/index.html", "verified", None),
    ({"intended_target": "app/dist/index.html", "staged_artifact_ref": "staging/index.html"}, "staging/index.html", "awaiting_verification", None),
    ({"intended_target": "app/dist/../secret.html", "staged_artifact_ref": "staging/index.html"}, "staging/index.html", "verified", None),
    ({"intended_target": "app/dist/index.html", "staged_artifact_ref": "staging/index.html"}, "staging/index.html", "verified", "0" * 64),
])
def test_promoted_manifest_requires_exact_verified_promotion_evidence(real_promoted_manifest, evidence, artifact_ref, state, verified_hash):
    workspace, repository = real_promoted_manifest
    digest = hashlib.sha256((workspace / "app" / "dist" / "index.html").read_bytes()).hexdigest()
    _write_manifest_deliverable(repository, evidence=evidence, artifact_ref=artifact_ref, digest=digest, state=state, verified_hash=verified_hash)
    assert preview_routes._promoted_manifest("tenant_1", "project_1") is None


def test_promoted_manifest_rejects_fabricated_verified_state_without_durable_human_verification(real_promoted_manifest):
    workspace, repository = real_promoted_manifest
    digest = hashlib.sha256((workspace / "app" / "dist" / "index.html").read_bytes()).hexdigest()
    rooms = CollaborationService(JsonCollaborationRepository(preview_routes._rooms_root))
    rooms.create_room(tenant_id="tenant_1", project_id="project_1", creator_id="human_verifier")
    missions = MissionService(repository)
    mission = missions.bootstrap("tenant_1", "project_1", "human_verifier", {
        "title": "Preview Mission", "objective": "Publish an approved app", "verifier_ids": ["human_verifier"],
    })
    fabricated = Deliverable(
        id="deliverable_fabricated", tenant_id="tenant_1", project_id="project_1", mission_id=mission.id,
        type="application", name="Preview app", producer_id="agent_1", version=1, content_hash=digest,
        source_ref="mission/agent", artifact_ref="staging/index.html",
        validation_evidence=[{"staged_artifact_ref": "staging/index.html", "intended_target": "app/dist/index.html"}],
        state="verified", verified_hash=digest,
    )
    repository.mutate("tenant_1", "project_1", lambda records: records["deliverables"].update({fabricated.id: fabricated.to_dict()}))
    assert preview_routes._promoted_manifest("tenant_1", "project_1") is None


def test_promoted_manifest_requires_the_durable_mission_verifier_not_only_a_room_member(real_promoted_manifest):
    workspace, repository = real_promoted_manifest
    digest = hashlib.sha256((workspace / "app" / "dist" / "index.html").read_bytes()).hexdigest()
    rooms = CollaborationService(JsonCollaborationRepository(preview_routes._rooms_root))
    room = rooms.create_room(tenant_id="tenant_1", project_id="project_1", creator_id="human_verifier")
    rooms.add_member(
        tenant_id="tenant_1", project_id="project_1", actor_id="human_verifier", member_id="other_reviewer",
        role="reviewer", expected_revision=room.revision,
    )
    missions = MissionService(repository)
    mission = missions.bootstrap("tenant_1", "project_1", "human_verifier", {
        "title": "Preview Mission", "objective": "Publish an approved app", "verifier_ids": ["human_verifier"],
    })
    fabricated = Deliverable(
        id="deliverable_wrong_verifier", tenant_id="tenant_1", project_id="project_1", mission_id=mission.id,
        type="application", name="Preview app", producer_id="agent_1", version=1, content_hash=digest,
        source_ref="mission/agent", artifact_ref="staging/index.html",
        validation_evidence=[{"staged_artifact_ref": "staging/index.html", "intended_target": "app/dist/index.html"}],
        state="verified", verified_by="other_reviewer", verified_hash=digest, verified_at="2026-08-28T00:00:00+00:00",
    )
    repository.mutate("tenant_1", "project_1", lambda records: records["deliverables"].update({fabricated.id: fabricated.to_dict()}))
    assert preview_routes._promoted_manifest("tenant_1", "project_1") is None


def test_promoted_manifest_uses_real_human_verification_service_path(real_promoted_manifest):
    workspace, repository = real_promoted_manifest
    content = (workspace / "app" / "dist" / "index.html").read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    rooms = CollaborationService(JsonCollaborationRepository(preview_routes._rooms_root))
    rooms.create_room(tenant_id="tenant_1", project_id="project_1", creator_id="human_verifier")
    missions = MissionService(repository)
    mission = missions.bootstrap("tenant_1", "project_1", "human_verifier", {
        "title": "Preview Mission",
        "objective": "Publish an approved app",
        "verifier_ids": ["human_verifier"],
    })
    created = missions.create_deliverable("tenant_1", "project_1", {
        "type": "application",
        "name": "Preview app",
        "source_ref": "mission/agent",
        "artifact_ref": "staging/index.html",
        "validation_evidence": [{
            "staged_artifact_ref": "staging/index.html",
            "intended_target": "app/dist/index.html",
        }],
    }, producer_id="agent_1", artifact_bytes=content)
    verified = missions.verify_deliverable(
        "tenant_1", "project_1", created.id, "human_verifier", digest, created.revision,
    )
    assert verified.mission_id == mission.id
    assert verified.verified_by == "human_verifier"
    assert verified.verified_at
    promoted = preview_routes._promoted_manifest("tenant_1", "project_1")
    assert promoted is not None
    revision, manifest = promoted
    assert manifest == {"index.html": digest}
    assert revision == hashlib.sha256(json.dumps([("index.html", digest, verified.version)], separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
    (workspace / "app" / "dist" / "index.html").write_text("changed", encoding="utf-8")
    assert preview_routes._promoted_manifest("tenant_1", "project_1") is None


def test_promoted_verifier_removal_is_linearized_with_exchange_publication(
    real_promoted_manifest, monkeypatch,
):
    workspace, mission_repository = real_promoted_manifest
    room_repository = JsonCollaborationRepository(preview_routes._rooms_root)
    room = CollaborationService(room_repository).create_room(
        tenant_id="tenant_1", project_id="project_1", creator_id="viewer",
    )
    CollaborationService(room_repository).add_member(
        tenant_id="tenant_1", project_id="project_1", actor_id="viewer",
        member_id="human_verifier", role="reviewer", expected_revision=room.revision,
    )
    missions = MissionService(mission_repository)
    missions.bootstrap("tenant_1", "project_1", "viewer", {
        "title": "Preview Mission", "objective": "Publish an approved app",
        "verifier_ids": ["human_verifier"],
    })
    content = (workspace / "app" / "dist" / "index.html").read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    created = missions.create_deliverable("tenant_1", "project_1", {
        "type": "application", "name": "Preview app", "source_ref": "mission/agent",
        "artifact_ref": "staging/index.html", "validation_evidence": [{
            "staged_artifact_ref": "staging/index.html", "intended_target": "app/dist/index.html",
        }],
    }, producer_id="agent_1", artifact_bytes=content)
    missions.verify_deliverable(
        "tenant_1", "project_1", created.id, "human_verifier", digest, created.revision,
    )
    monkeypatch.setenv("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", "true")
    monkeypatch.setenv("CONTROL_ORIGIN", "https://app.example.test")
    monkeypatch.setenv("PREVIEW_ORIGIN", "https://preview.example.test")
    monkeypatch.setenv("PREVIEW_REGISTRABLE_DOMAIN", "example.test")
    monkeypatch.setenv("CMUL8_PREVIEW_EXCHANGE_SECRET", "a test-only exchange signing secret")
    monkeypatch.setattr(
        preview_routes, "_exchange_root",
        preview_routes.RUNS_DIR / ".workplace-control" / "preview-exchanges",
    )
    entered, release, removal_finished = threading.Event(), threading.Event(), threading.Event()
    original_write = preview_routes._write_exchange_state

    def blocked_write(directory, state):
        entered.set()
        assert release.wait(timeout=5)
        return original_write(directory, state)

    def remove_verifier():
        current = room_repository.get_room("tenant_1", "project_1")
        room_repository.save_room(replace(
            current,
            members=[member for member in current.members if member.actor_id != "human_verifier"],
            revision=current.revision + 1,
        ), current.revision)
        removal_finished.set()

    monkeypatch.setattr(preview_routes, "_write_exchange_state", blocked_write)
    with ThreadPoolExecutor(max_workers=2) as pool:
        publication = pool.submit(
            preview_routes.create_exchange, "project_1", preview_routes._ExchangeBody(),
            _request(host="app.example.test"), _context("viewer"),
        )
        assert entered.wait(timeout=5)
        removal = pool.submit(remove_verifier)
        assert not removal_finished.wait(timeout=0.1)
        release.set()
        assert publication.result(timeout=5)["exchange_id"]
        removal.result(timeout=5)
    assert preview_routes._promoted_manifest("tenant_1", "project_1") is None
    with pytest.raises(HTTPException) as removed_first:
        preview_routes.create_exchange(
            "project_1", preview_routes._ExchangeBody(), _request(host="app.example.test"),
            _context("viewer"),
        )
    assert removed_first.value.status_code == 404


def test_preview_vite_relative_base_keeps_nested_assets_under_capability_route():
    config = (Path(__file__).parents[1] / "apps" / "console" / "vite.config.ts").read_text(encoding="utf-8")
    assert 'mode === "preview"' in config and 'base: mode === "preview"' in config and ' ? "./" ' in config


async def _response(status_code: int):
    return preview_routes.Response(status_code=status_code)
