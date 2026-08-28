from __future__ import annotations

import asyncio
import copy
import io
import inspect
import json
import queue
import threading
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import ValidationError

from apps.api import cmul8_routes, main as api_main
from simulacra.demo.identity import AuthContext, User
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.operation_graph import load_operation_graph
from simulacra.collaboration.models import Member, ReviewDecision, Task, TaskState


def _context() -> AuthContext:
	return AuthContext(
		user=User(id="user_owner", email="owner@example.test", name="Owner", password_hash="unused"),
		tenant_id="tenant_api", role="owner", auth_via="test",
	)


def _member_context() -> AuthContext:
	return AuthContext(
		user=User(id="user_member", email="member@example.test", name="Member", password_hash="unused"),
		tenant_id="tenant_api", role="member", auth_via="test",
	)


def _admin_context() -> AuthContext:
	return AuthContext(
		user=User(id="user_admin", email="admin@example.test", name="Admin", password_hash="unused"),
		tenant_id="tenant_api", role="admin", auth_via="test",
	)


def _room_context(user_id: str) -> AuthContext:
	return AuthContext(
		user=User(id=user_id, email=f"{user_id}@example.test", name=user_id, password_hash="unused"),
		tenant_id="tenant_api", role="member", auth_via="test",
	)


def _prepare(monkeypatch, tmp_path: Path) -> Path:
	project_root = tmp_path / "project_api"
	project_root.mkdir()
	monkeypatch.setattr(cmul8_routes, "_collaboration_root", tmp_path / "control")
	monkeypatch.setattr(api_main, "_collaboration_root", tmp_path / "control")
	monkeypatch.setattr(cmul8_routes, "_telemetry_root", tmp_path / "telemetry")
	monkeypatch.setattr(cmul8_routes, "_runtime_root", tmp_path / "runtime")
	monkeypatch.setattr(cmul8_routes, "project_dir", lambda _project_id: project_root)
	monkeypatch.setattr(
		cmul8_routes, "load_state",
		lambda _project_id: SimpleNamespace(
			tenant_id="tenant_api",
			app_config=SimpleNamespace(title="Support operations"),
			goal="Coordinate case resolution",
			prompt="",
		),
	)
	monkeypatch.setattr(cmul8_routes, "audit_request", lambda *args, **kwargs: None)
	return project_root


def test_public_project_snapshot_and_event_feeds_exclude_execution_metadata(monkeypatch):
	ctx = _context()
	raw_snapshot = {
		"project": {
			"id": "project_api", "prompt": "Prepare a brief", "goal": "Brief", "phase": "plan", "plan_approved": False,
			"status": "ready", "artifact_kind": "report", "gates_status": "ready", "deployed": False,
			"deploy_url": None, "chat": [], "app_config": {"title": "Brief"}, "row_count": 0, "checkpoints": [],
			"active_checkpoint": 0, "plan_preview": {"row_count": 0, "summary": "Ready", "model": "private", "fingerprint": "digest-secret"},
			"chats": [{"id": "chat_1", "title": "Brief", "messages": [{"role": "assistant", "content": "Safe reply", "source": "prime", "runtime": "private"}]}],
			"prime": {"session_id": "session_1", "session_dir": "/private", "model": "private", "steps": 9, "duration_ms": 20, "status": "idle", "request": "await_user"},
			"job": {"id": "job_1", "status": "running", "steps": 2, "max_steps": 10},
			"sandbox": {"runtime": "private", "provider": "private"},
		},
		"preview_data": {"columns": ["amount"], "rows": [{"amount": 1}], "row_count": 1}, "preview_url": None,
		"job": {"id": "job_1", "status": "running", "steps": 2, "max_steps": 10},
	}
	monkeypatch.setattr(api_main, "project_snapshot", lambda _project_id: raw_snapshot)
	public = api_main.get_project("project_api", ctx)
	assert "prime" not in public["project"] and "job" not in public["project"] and "job" not in public
	assert public["project"]["plan_preview"] == {"row_count": 0, "summary": "Ready"}
	assert "sandbox" not in public["project"]
	assert public["project"]["chats"][0]["messages"] == [{"role": "assistant", "content": "Safe reply"}]
	serialized = json.dumps(public).lower()
	for forbidden in ("session_id", "session_dir", "model", "steps", "duration", "sandbox", "runtime", "provider", "max_steps", "prime", "job", "fingerprint", "digest"):
		assert forbidden not in serialized

	raw_event = {
		"id": "evt_1", "ts": "now", "type": "tool", "label": "private tool", "detail": "args={secret}", "status": "running",
		"meta": {"tool": "private_tool", "toolCallId": "call_1", "args": {"secret": "x"}, "result": "hidden", "runtime": "private"},
	}
	monkeypatch.setattr(api_main, "list_events", lambda _project_id: [raw_event])
	polled = api_main.get_events("project_api", ctx)["events"]
	assert polled == [{"id": "evt_1", "ts": "now", "type": "phase", "label": "Mission work", "detail": "Progress update", "status": "running"}]
	q: queue.Queue[dict] = queue.Queue(); q.put(raw_event)
	monkeypatch.setattr(api_main, "subscribe", lambda _project_id: q)
	monkeypatch.setattr(api_main, "unsubscribe", lambda _project_id, _q: None)

	async def first_sse_payload() -> str:
		response = await api_main.stream_events("project_api", ctx)
		try:
			return await anext(response.body_iterator)
		finally:
			await response.body_iterator.aclose()

	chunk = asyncio.run(first_sse_payload())
	assert chunk == 'data: {"id": "evt_1", "ts": "now", "type": "phase", "label": "Mission work", "detail": "Progress update", "status": "running"}\n\n'


def test_public_sources_omit_fingerprints_and_file_items_use_public_checksums(monkeypatch):
	ctx = _context()
	checksum = "a" * 64
	legacy = {"name": "cases.csv", "size": 42, "type": "text/csv", "status": "ready", "detail": "Uploaded", "row_count": 1}
	file_item = {
		"id": "file_" + "b" * 40, "mission_id": "project_api", "kind": "source", "name": "cases.csv",
		"media_type": "text/csv", "size": 42, "sha256": checksum, "state": "ready", "version": 1,
		"producer_id": None, "producer": None, "verifier": None, "run_id": None, "parent_output_id": None,
		"source_ids": [], "introduced_by_message_id": None, "created_at": None, "updated_at": None,
		"previewable": True, "downloadable": True,
	}
	monkeypatch.setattr(api_main, "authorized_file_inventory", lambda *_args, **_kwargs: {"items": [file_item], "files": [legacy]})
	monkeypatch.setattr(api_main, "load_state", lambda _project_id: SimpleNamespace(
		plan_preview={"profile": {"fingerprint": "hidden"}, "extract": {"row_count": 1}}, row_count=1,
	))
	files = api_main.project_files("project_api", ctx)
	source_view = api_main.get_sources("project_api", ctx)
	assert files["items"][0]["sha256"] == checksum
	for payload in (source_view, api_main._public_plan_preview({"files": [{**legacy, "sha256": "secret-digest"}], "fingerprint": "hidden"})):
		serialized = json.dumps(payload).lower()
		assert "sha256" not in serialized and "digest" not in serialized and "fingerprint" not in serialized
	assert source_view["files"] == [legacy]


def test_router_is_mounted_with_tenant_scoped_contracts():
	app = FastAPI()
	app.include_router(cmul8_routes.router)
	paths = {route.path for route in cmul8_routes.router.routes if hasattr(route, "path")}
	assert "/projects/{project_id}/cmul8/room" in paths
	assert "/projects/{project_id}/cmul8/tasks" in paths
	assert "/projects/{project_id}/cmul8/comments" in paths
	assert "/projects/{project_id}/cmul8/operation-graph/revisions" in paths
	assert "/projects/{project_id}/cmul8/room/presence" in paths
	assert "/projects/{project_id}/cmul8/presence" not in paths
	assert "body" not in inspect.signature(cmul8_routes.heartbeat_presence).parameters
	client = (Path(__file__).parents[1] / "apps/console/src/api.ts").read_text(encoding="utf-8")
	heartbeat_client = client.split("export async function heartbeatCmul8Presence", 1)[1].split("export async function", 1)[0]
	assert 'method: "POST"' in heartbeat_client and "body:" not in heartbeat_client


def test_room_view_requires_current_room_membership(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, ctx)
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.get_room("project_api", _room_context("outsider"))
	assert denied.value.status_code == 403


def test_invitation_accept_dependency_allows_pre_membership_but_returns_anti_enumeration_errors(monkeypatch, tmp_path):
	"""Acceptance has a narrow verified identity path, never a tenant session."""
	_prepare(monkeypatch, tmp_path)
	from apps.api.security import InvitationAcceptPrincipal
	from simulacra.demo import identity, tenants
	from simulacra.collaboration.models import Invitation
	from datetime import UTC, datetime, timedelta
	import hashlib

	data = tmp_path / "identity"; data.mkdir()
	monkeypatch.setattr(identity, "DATA_DIR", data); monkeypatch.setattr(identity, "USERS_PATH", data / "users.json")
	monkeypatch.setattr(identity, "MEMBERSHIPS_PATH", data / "memberships.json"); monkeypatch.setattr(tenants, "TENANTS_PATH", data / "tenants.json")
	tenant = tenants.create_tenant("API invite")
	monkeypatch.setattr(cmul8_routes, "load_state", lambda _project_id: SimpleNamespace(tenant_id=tenant.id))
	owner = identity.create_user("owner@example.test", "password12345"); invitee = identity.create_user("invitee@example.test", "password12345")
	identity.add_membership(tenant.id, owner.id, "owner")
	repository, service = cmul8_routes._collaboration()
	service.create_room(tenant_id=tenant.id, project_id="project_api", creator_id=owner.id)
	token = "z" * 32
	invitation = Invitation(id="invite_api", tenant_id=tenant.id, project_id="project_api", invited_by=owner.id,
		invitee_email=invitee.email, requested_role="member", accept_token_digest=hashlib.sha256(token.encode()).hexdigest(),
		status="pending", expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
	repository.create_invitation(invitation)
	principal = InvitationAcceptPrincipal(actor_id=invitee.id, verified_email=invitee.email, provider_subject="provider_invitee")
	accepted = cmul8_routes.accept_invitation("project_api", invitation.id,
		cmul8_routes.InvitationAcceptBody(client_request_id="accept_1", token=token), principal)
	assert accepted["membership"]["actor_id"] == invitee.id
	with pytest.raises(HTTPException) as unavailable:
		cmul8_routes.accept_invitation("project_api", "invite_missing",
			cmul8_routes.InvitationAcceptBody(client_request_id="accept_2", token=token), principal)
	assert unavailable.value.status_code == 404 and unavailable.value.detail["code"] == "invitation_unavailable"


def test_presence_two_humans_are_scoped_and_membership_filtered(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test")); owner = _context(); member = _member_context()
	monkeypatch.setattr(cmul8_routes, "_presence", cmul8_routes.PresenceRegistry(ttl_seconds=181))
	cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)
	repository, service = cmul8_routes._collaboration()
	service.add_member(tenant_id="tenant_api", project_id="project_api", actor_id=owner.user.id, member_id=member.user.id, role="member", expected_revision=1)
	owner_heartbeat = cmul8_routes.heartbeat_presence("project_api", owner)
	member_heartbeat = cmul8_routes.heartbeat_presence("project_api", member)
	assert owner_heartbeat == {"presence": {"actor_id": owner.user.id, "status": "online", "last_seen_at": owner_heartbeat["presence"]["last_seen_at"]}}
	assert member_heartbeat["presence"]["actor_id"] == member.user.id
	presence = cmul8_routes.get_room("project_api", owner)["presence"]
	assert [(item["actor_id"], item["status"]) for item in presence] == [(member.user.id, "online"), (owner.user.id, "online")]
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.get_room("project_api", _room_context("outsider"))
	assert denied.value.status_code == 403


def test_pending_invitation_revoke_route_replays_and_rejects_body_or_target_reuse(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test")); owner = _context()
	cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)
	first = cmul8_routes.create_invitation(
		"project_api",
		cmul8_routes.InvitationCreateBody(client_request_id="create_first", email="first@example.test"),
		request, owner,
	)
	second = cmul8_routes.create_invitation(
		"project_api",
		cmul8_routes.InvitationCreateBody(client_request_id="create_second", email="second@example.test"),
		request, owner,
	)
	body = cmul8_routes.InvitationRevokeBody(client_request_id="revoke_first", expected_revision=1)
	revoked = cmul8_routes.revoke_invitation(
		"project_api", first["invitation"]["id"], body, request, owner,
	)
	assert cmul8_routes.revoke_invitation(
		"project_api", first["invitation"]["id"], body, request, owner,
	) == revoked
	assert revoked == {"invitation": {"id": first["invitation"]["id"], "status": "revoked", "revision": 2}}
	assert "token" not in json.dumps(revoked) and "digest" not in json.dumps(revoked)

	for invitation_id, expected_revision in (
		(first["invitation"]["id"], 2),
		(second["invitation"]["id"], 1),
	):
		with pytest.raises(HTTPException) as mismatch:
			cmul8_routes.revoke_invitation(
				"project_api", invitation_id,
				cmul8_routes.InvitationRevokeBody(
					client_request_id="revoke_first", expected_revision=expected_revision,
				),
				request, owner,
			)
		assert mismatch.value.status_code == 409
		assert mismatch.value.detail["code"] == "idempotency_mismatch"


def test_member_remove_route_requires_owner_admin_and_current_room_revision(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test")); owner = _context(); member = _member_context()
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	room = cmul8_routes.add_room_member(
		"project_api", cmul8_routes.RoomMemberBody(member_id=member.user.id, expected_revision=room["revision"]), request, owner,
	)
	repository, _service = cmul8_routes._collaboration()
	raw_room = repository.get_room("tenant_api", "project_api")
	repository.save_room(
		replace(
			raw_room,
			members=[*raw_room.members, Member(
				actor_id="pending_human", role="member", transaction_id="txn_incomplete",
				visibility_state="pending_commit",
			)],
			revision=raw_room.revision + 1,
		),
		raw_room.revision,
	)
	room = cmul8_routes.get_room("project_api", owner)["room"]
	assert "pending_human" not in {item["actor_id"] for item in room["members"]}

	with pytest.raises(HTTPException) as denied:
		cmul8_routes.remove_room_member(
			"project_api", owner.user.id,
			cmul8_routes.MemberRemoveBody(client_request_id="member_cannot", expected_room_revision=room["revision"]),
			request, member,
		)
	assert denied.value.status_code == 403
	with pytest.raises(HTTPException) as stale:
		cmul8_routes.remove_room_member(
			"project_api", member.user.id,
			cmul8_routes.MemberRemoveBody(client_request_id="stale_remove", expected_room_revision=1),
			request, owner,
		)
	assert stale.value.status_code == 409

	removed = cmul8_routes.remove_room_member(
		"project_api", member.user.id,
		cmul8_routes.MemberRemoveBody(client_request_id="remove_member", expected_room_revision=room["revision"]),
		request, owner,
	)
	replayed = cmul8_routes.remove_room_member(
		"project_api", member.user.id,
		cmul8_routes.MemberRemoveBody(client_request_id="remove_member", expected_room_revision=room["revision"]),
		request, owner,
	)
	assert replayed == removed
	assert member.user.id not in {item["actor_id"] for item in removed["members"]}
	assert "pending_human" not in {item["actor_id"] for item in removed["members"]}
	assert "mutation_receipts" not in json.dumps(removed)
	with pytest.raises(HTTPException) as mismatch:
		cmul8_routes.remove_room_member(
			"project_api", "different_member",
			cmul8_routes.MemberRemoveBody(client_request_id="remove_member", expected_room_revision=room["revision"]),
			request, owner,
		)
	assert mismatch.value.status_code == 409
	assert mismatch.value.detail["code"] == "idempotency_mismatch"


def test_hidden_pending_admin_is_denied_and_hidden_removal_target_is_preserved(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test")); owner = _context()
	cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)
	repository, service = cmul8_routes._collaboration()
	raw = repository.get_room("tenant_api", "project_api")
	raw = service.add_member(
		tenant_id="tenant_api", project_id="project_api", actor_id=owner.user.id,
		member_id="committed_admin", role="admin", expected_revision=raw.revision,
	)
	repository.save_room(
		replace(
			raw,
			members=[*raw.members, Member(
				actor_id="pending_admin", role="owner", transaction_id="txn_pending_admin",
				visibility_state="pending_commit",
			)],
			revision=raw.revision + 1,
		),
		raw.revision,
	)
	pending = _room_context("pending_admin")
	with pytest.raises(HTTPException) as heartbeat_denied:
		cmul8_routes.heartbeat_presence("project_api", pending)
	assert heartbeat_denied.value.status_code == 403
	with pytest.raises(HTTPException) as task_denied:
		cmul8_routes.create_task(
			"project_api", cmul8_routes.TaskCreateBody(
				title="Hidden mutation", objective="Must not run", acceptance_criteria=["denied"],
			),
			request, pending,
		)
	assert task_denied.value.status_code == 403
	with pytest.raises(HTTPException) as plan_denied:
		cmul8_routes._require_graph_mutator("project_api", pending)
	assert plan_denied.value.status_code == 403
	with pytest.raises(HTTPException) as invitation_denied:
		cmul8_routes.create_invitation(
			"project_api", cmul8_routes.InvitationCreateBody(
				client_request_id="hidden_invite", email="new@example.test",
			),
			request, pending,
		)
	assert invitation_denied.value.status_code == 403

	current_revision = repository.get_room("tenant_api", "project_api").revision
	with pytest.raises(HTTPException) as hidden_target:
		cmul8_routes.remove_room_member(
			"project_api", "pending_admin",
			cmul8_routes.MemberRemoveBody(
				client_request_id="hidden_target", expected_room_revision=current_revision,
			),
			request, _room_context("committed_admin"),
		)
	assert hidden_target.value.status_code == 404
	assert any(member.actor_id == "pending_admin" for member in repository.get_room("tenant_api", "project_api").members)


def test_room_task_and_graph_are_durable_not_synthesized(monkeypatch, tmp_path):
	project_root = _prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	created = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, ctx)
	assert set(created["room"]) == {"id", "members", "revision", "created_at", "updated_at"}
	assert created["room"]["members"][0]["actor_id"] == "user_owner"
	assert created["room"]["members"][0]["display_name"] == "Owner"
	assert created["room"]["members"][0]["actor_type"] == "human"

	task = cmul8_routes.create_task(
		"project_api",
		cmul8_routes.TaskCreateBody(
			title="Review case", objective="Independently review the case",
			acceptance_criteria=["Decision recorded"], owner_id="user_owner",
		),
		request, ctx,
	)
	assert task["owner_id"] == "user_owner"
	loaded = cmul8_routes.get_room("project_api", ctx)
	assert [item["id"] for item in loaded["tasks"]] == [task["id"]]
	proposed = cmul8_routes.create_task(
		"project_api", cmul8_routes.TaskCreateBody(
			title="Claim me", objective="Atomically assign work", acceptance_criteria=["Owner recorded"],
		), request, ctx,
	)
	claimed = cmul8_routes.claim_task("project_api", proposed["id"], proposed["revision"], request, ctx)
	assert claimed["owner_id"] == "user_owner" and claimed["state"] == "ready"

	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_api"
	graph["metadata"]["project_id"] = "project_api"
	store = cmul8_routes.OperationGraphStore(project_root, tenant_id="tenant_api", project_id="project_api")
	revision = store.create_revision(graph, expected_revision_hash=None)
	with pytest.raises(HTTPException) as stale:
		cmul8_routes.approve_current_graph_revision("project_api", cmul8_routes.CurrentPlanApprovalBody(expected_revision=revision.revision + 1), request, ctx)
	assert stale.value.status_code == 409
	approved = cmul8_routes.approve_current_graph_revision("project_api", cmul8_routes.CurrentPlanApprovalBody(expected_revision=revision.revision), request, ctx)
	assert approved == {"revision": revision.revision, "status": "approved"}
	comment = cmul8_routes.create_comment("project_api", cmul8_routes.CommentCreateBody(body="Please confirm the handoff.", plan_revision=revision.revision), request, ctx)
	assert set(comment) <= {"id", "author_id", "body", "status", "plan_revision", "created_at", "updated_at"}
	assert comment["plan_revision"] == revision.revision and comment["status"] == "posted"
	plan = cmul8_routes.get_room("project_api", ctx)["mission_plan"]
	assert plan == {"revision": revision.revision, "objective": "Routes priority cases through governed resolution.", "steps": ["Resolve case"], "human_checkpoints": ["Approve external replies"], "status": "approved"}
	assert "revision_hash" not in str(plan) and "connector" not in str(plan).lower()
	# The old immutable plan can become current again after a rollback.  Its
	# historical graph revision must not authorize an approval for the new head.
	changed_graph = copy.deepcopy(graph)
	changed_graph["metadata"]["description"] = "Routes cases through a revised Mission plan."
	changed = store.create_revision(changed_graph, expected_revision_hash=revision.revision_hash)
	rolled_back = store.rollback_to(
		revision.revision_hash, expected_revision_hash=changed.revision_hash,
		actor_id="user_owner", reason="Restore reviewed plan",
	)
	assert rolled_back.revision > revision.revision
	with pytest.raises(HTTPException) as aba_stale:
		cmul8_routes.approve_current_graph_revision(
			"project_api", cmul8_routes.CurrentPlanApprovalBody(expected_revision=revision.revision), request, ctx,
		)
	assert aba_stale.value.status_code == 409
	rolled_back_plan = cmul8_routes.get_room("project_api", ctx)["mission_plan"]
	assert rolled_back_plan["revision"] == rolled_back.revision
	assert cmul8_routes.approve_current_graph_revision(
		"project_api", cmul8_routes.CurrentPlanApprovalBody(expected_revision=rolled_back.revision), request, ctx,
	) == {"revision": rolled_back.revision, "status": "approved"}
	with pytest.raises(ValidationError):
		cmul8_routes.TaskCreateBody(title="Spoof", objective="No spoofed plan revision", acceptance_criteria=["Bound server-side"], operation_graph_version="spoof")
	bound = cmul8_routes.create_task(
		"project_api", cmul8_routes.TaskCreateBody(title="Bound", objective="Use current approved plan", acceptance_criteria=["Server bound"]), request, ctx,
	)
	stored = cmul8_routes.JsonCollaborationRepository(cmul8_routes._collaboration_root).get_task("tenant_api", "project_api", bound["id"])
	assert stored.operation_graph_version == revision.revision_hash


def test_mission_plan_snapshot_keeps_head_and_plan_together_during_head_change(monkeypatch, tmp_path):
	project_root = _prepare(monkeypatch, tmp_path)
	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_api"
	graph["metadata"]["project_id"] = "project_api"
	store = cmul8_routes.OperationGraphStore(project_root, tenant_id="tenant_api", project_id="project_api")
	first = store.create_revision(graph, expected_revision_hash=None)
	store.approve_revision(first.revision_hash, actor_id="user_owner")
	second_graph = copy.deepcopy(graph)
	second_graph["metadata"]["description"] = "A revised Mission plan for the same work."
	second = store.create_revision(second_graph, expected_revision_hash=first.revision_hash)

	snapshot_reading = threading.Event()
	writer_started = threading.Event()
	original_load = store.load_revision

	def load_while_snapshot_is_locked(revision_hash: str):
		snapshot_reading.set()
		assert writer_started.wait(timeout=2)
		return original_load(revision_hash)

	monkeypatch.setattr(store, "load_revision", load_while_snapshot_is_locked)

	def rollback_head() -> None:
		assert snapshot_reading.wait(timeout=2)
		writer_started.set()
		store.rollback_to(first.revision_hash, expected_revision_hash=second.revision_hash, actor_id="user_owner", reason="Concurrent rollback")

	writer = threading.Thread(target=rollback_head)
	writer.start()
	snapshot = cmul8_routes._mission_plan_snapshot(store)
	writer.join(timeout=2)
	assert not writer.is_alive()
	assert snapshot == {
		"revision": second.revision,
		"objective": "A revised Mission plan for the same work.",
		"steps": ["Resolve case"],
		"human_checkpoints": ["Approve external replies"],
		"status": "pending_approval",
	}
	assert store.current_revision().revision_hash == first.revision_hash


def test_pending_mission_plan_blocks_task_creation_with_safe_conflict(monkeypatch, tmp_path):
	project_root = _prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, ctx)
	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_api"
	graph["metadata"]["project_id"] = "project_api"
	cmul8_routes.OperationGraphStore(project_root, tenant_id="tenant_api", project_id="project_api").create_revision(
		graph, expected_revision_hash=None,
	)
	with pytest.raises(HTTPException) as blocked:
		cmul8_routes.create_task(
			"project_api", cmul8_routes.TaskCreateBody(title="Plan-bound work", objective="Wait for plan approval", acceptance_criteria=["Approved plan"]), request, ctx,
		)
	assert blocked.value.status_code == 409
	assert blocked.value.detail == "Review and approve the current Mission plan before creating Mission work."
	assert "hash" not in blocked.value.detail.lower() and "operation graph" not in blocked.value.detail.lower()


def test_task_creation_holds_approved_plan_lock_through_durable_write(monkeypatch, tmp_path):
	project_root = _prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, ctx)
	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_api"
	graph["metadata"]["project_id"] = "project_api"
	store = cmul8_routes.OperationGraphStore(project_root, tenant_id="tenant_api", project_id="project_api")
	first = store.create_revision(graph, expected_revision_hash=None)
	store.approve_revision(first.revision_hash, actor_id="user_owner")
	second_graph = copy.deepcopy(graph)
	second_graph["metadata"]["description"] = "Newly approved Mission plan."
	second = store.create_revision(second_graph, expected_revision_hash=first.revision_hash)
	store.approve_revision(second.revision_hash, actor_id="user_owner")
	monkeypatch.setattr(cmul8_routes, "_graph_store", lambda _project_id, _tenant_id: store)

	repository, service = cmul8_routes._collaboration()
	task_started = threading.Event()
	rollback_started = threading.Event()
	rollback_finished = threading.Event()
	original_create_task = service.create_task

	def create_task_while_plan_is_pinned(**kwargs):
		task_started.set()
		assert rollback_started.wait(timeout=2)
		# The rollback has attempted to take the graph lock, but cannot finish
		# until this exact task has been persisted against the current plan.
		assert not rollback_finished.is_set()
		return original_create_task(**kwargs)

	monkeypatch.setattr(service, "create_task", create_task_while_plan_is_pinned)
	monkeypatch.setattr(cmul8_routes, "_collaboration", lambda: (repository, service))

	def rollback() -> None:
		assert task_started.wait(timeout=2)
		rollback_started.set()
		store.rollback_to(first.revision_hash, expected_revision_hash=second.revision_hash, actor_id="user_owner", reason="Replace current plan")
		rollback_finished.set()

	writer = threading.Thread(target=rollback)
	writer.start()
	task = cmul8_routes.create_task(
		"project_api", cmul8_routes.TaskCreateBody(title="Bound task", objective="Persist against one approved plan", acceptance_criteria=["Plan stays current through write"]), request, ctx,
	)
	writer.join(timeout=2)
	assert not writer.is_alive() and rollback_finished.is_set()
	stored = repository.get_task("tenant_api", "project_api", task["id"])
	assert stored.operation_graph_version == second.revision_hash
	assert store.current_revision().revision_hash == first.revision_hash


def test_room_payload_does_not_invent_deployment_health(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	payload = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, ctx)
	assert "deployments" not in payload
	assert payload["permissions"]["review_graph"] is True
	cmul8_routes.heartbeat_presence("project_api", ctx)
	room = cmul8_routes.get_room("project_api", ctx)
	assert room["presence"][0]["actor_id"] == "user_owner"
	assert all("payload" not in event and "trace_id" not in event and "environment_id" not in event for event in room["events"])


def test_public_harness_status_is_a_product_health_dto(monkeypatch):
	ctx = _context()
	monkeypatch.setattr(cmul8_routes.HarnessConfig, "from_env", staticmethod(lambda: object()))

	class Harness:
		async def healthcheck(self):
			return {"ok": True, "provider": "private", "model": "private", "profile": "internal"}

	monkeypatch.setattr(cmul8_routes, "create_harness", lambda _config: Harness())
	assert asyncio.run(cmul8_routes.harness_status("project_api", ctx)) == {"status": "available"}


def test_member_cannot_bootstrap_an_ownerless_room(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, _member_context())
	assert denied.value.status_code == 403
	created = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, _context())
	assert created["room"]["members"][0]["role"] == "owner"


def test_tenant_admin_bootstrap_is_seeded_as_the_initial_room_owner(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	created = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, _admin_context())

	assert [(member["actor_id"], member["role"]) for member in created["room"]["members"]] == [("user_admin", "owner")]


def test_room_invite_resolves_workspace_teammate_by_email(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	owner = _context()
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	teammate = User(id="user_finance", email="finance@example.test", name="Priya Shah", password_hash="unused")
	monkeypatch.setattr(cmul8_routes, "get_user_by_email", lambda email: teammate if email == teammate.email else None)
	monkeypatch.setattr(cmul8_routes, "get_membership", lambda tenant_id, user_id: object() if (tenant_id, user_id) == ("tenant_api", teammate.id) else None)
	monkeypatch.setattr(cmul8_routes, "get_user", lambda user_id: teammate if user_id == teammate.id else owner.user)

	updated = cmul8_routes.add_room_member(
		"project_api", cmul8_routes.RoomMemberBody(member_email="FINANCE@example.test", role="reviewer", expected_revision=room["revision"]), request, owner,
	)
	added = next(member for member in updated["members"] if member["actor_id"] == teammate.id)
	assert added["display_name"] == "Priya Shah"
	assert added["role"] == "reviewer"


def test_room_invite_by_email_requires_workspace_membership(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	owner = _context()
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	outsider = User(id="user_outside", email="outside@example.test", name="Outside", password_hash="unused")
	monkeypatch.setattr(cmul8_routes, "get_user_by_email", lambda _email: outsider)
	monkeypatch.setattr(cmul8_routes, "get_membership", lambda _tenant_id, _user_id: None)

	with pytest.raises(HTTPException) as denied:
		cmul8_routes.add_room_member(
			"project_api", cmul8_routes.RoomMemberBody(member_email=outsider.email, expected_revision=room["revision"]), request, owner,
		)
	assert denied.value.status_code == 403


def test_project_room_roles_control_mutations_reviews_and_durable_review_roles(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	owner, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	for member_id, role in (("viewer", "viewer"), ("reviewer", "reviewer"), ("approver", "approver")):
		room = cmul8_routes.add_room_member(
			"project_api", cmul8_routes.RoomMemberBody(member_id=member_id, role=role, expected_revision=room["revision"]), request, owner,
		)

	with pytest.raises(HTTPException) as denied:
		cmul8_routes.create_task(
			"project_api", cmul8_routes.TaskCreateBody(title="Viewer task", objective="must fail", acceptance_criteria=["no write"]),
			request, _room_context("viewer"),
		)
	assert denied.value.status_code == 403
	unowned = cmul8_routes.create_task(
		"project_api", cmul8_routes.TaskCreateBody(title="Unowned", objective="claim control", acceptance_criteria=["owner"]),
		request, owner,
	)
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.claim_task("project_api", unowned["id"], unowned["revision"], request, _room_context("viewer"))
	assert denied.value.status_code == 403
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.transition_task(
			"project_api", unowned["id"],
			cmul8_routes.TaskTransitionBody(state="ready", expected_revision=unowned["revision"]), request, _room_context("viewer"),
		)
	assert denied.value.status_code == 403
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.create_comment(
			"project_api", cmul8_routes.CommentCreateBody(body="viewer write"), request, _room_context("viewer"),
		)
	assert denied.value.status_code == 403

	def task_in_review(title: str) -> dict:
		task = cmul8_routes.create_task(
			"project_api", cmul8_routes.TaskCreateBody(
				title=title, objective="Review durable room authority", acceptance_criteria=["Decision"], owner_id="user_owner",
			), request, owner,
		)
		task = cmul8_routes.transition_task(
			"project_api", task["id"], cmul8_routes.TaskTransitionBody(state="working", expected_revision=task["revision"]), request, owner,
		)
		return cmul8_routes.transition_task(
			"project_api", task["id"], cmul8_routes.TaskTransitionBody(state="in_review", expected_revision=task["revision"]), request, owner,
		)

	for member_id, expected_role in (("reviewer", "reviewer"), ("approver", "approver")):
		task = task_in_review(f"{expected_role} review")
		result = cmul8_routes.review_task(
			"project_api", task["id"],
			cmul8_routes.TaskReviewBody(decision="approve", expected_revision=task["revision"]),
			request, _room_context(member_id),
		)
		assert result["review"]["role"] == expected_role
		assert "tenant_id" not in result["review"] and "schema_version" not in result["review"]
		assert result["task"]["state"] == "done"
		event = [event for event in cmul8_routes.get_room("project_api", owner)["events"] if event["action"] == "task.reviewed"][-1]
		assert event["reviewer_role"] == expected_role

	task = task_in_review("nonmember review")
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.review_task(
			"project_api", task["id"],
			cmul8_routes.TaskReviewBody(decision="approve", expected_revision=task["revision"]),
			request, _room_context("nonmember"),
		)
	assert denied.value.status_code == 403


def test_in_review_task_can_only_complete_through_distinct_reviewer_route(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	owner, reviewer = _context(), _room_context("reviewer")
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	cmul8_routes.add_room_member(
		"project_api",
		cmul8_routes.RoomMemberBody(member_id="reviewer", role="reviewer", expected_revision=room["revision"]),
		request,
		owner,
	)
	task = cmul8_routes.create_task(
		"project_api",
		cmul8_routes.TaskCreateBody(
			title="Require independent approval", objective="Complete only after review",
			acceptance_criteria=["A distinct reviewer approves"], owner_id="user_owner",
		),
		request,
		owner,
	)
	task = cmul8_routes.transition_task(
		"project_api", task["id"],
		cmul8_routes.TaskTransitionBody(state=TaskState.WORKING, expected_revision=task["revision"]),
		request, owner,
	)
	task = cmul8_routes.transition_task(
		"project_api", task["id"],
		cmul8_routes.TaskTransitionBody(state=TaskState.IN_REVIEW, expected_revision=task["revision"]),
		request, owner,
	)

	with pytest.raises(HTTPException) as blocked:
		cmul8_routes.transition_task(
			"project_api", task["id"],
			cmul8_routes.TaskTransitionBody(state=TaskState.DONE, expected_revision=task["revision"]),
			request, owner,
		)
	assert blocked.value.status_code == 409
	assert blocked.value.detail == {
		"code": "mission_conflict",
		"message": "That Mission item changed. Refresh and try again.",
	}

	approved = cmul8_routes.review_task(
		"project_api", task["id"],
		cmul8_routes.TaskReviewBody(decision=ReviewDecision.APPROVE, expected_revision=task["revision"]),
		request, reviewer,
	)
	assert approved["review"]["author_id"] == "reviewer"
	assert approved["task"]["state"] == TaskState.DONE


def test_legacy_graph_control_routes_are_retired_from_public_use(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	with pytest.raises(ValidationError):
		cmul8_routes.GraphRevisionBody(graph={"raw": "plan"})
	with pytest.raises(HTTPException) as retired:
		cmul8_routes.create_graph_revision("project_api", cmul8_routes.GraphRevisionBody(), request, ctx)
	assert retired.value.status_code == 410
	with pytest.raises(HTTPException) as retired:
		cmul8_routes.approve_graph_revision("project_api", "not-public", request, ctx)
	assert retired.value.status_code == 410


def test_legacy_graph_approval_and_build_require_the_same_durable_room_role(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	owner, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	for member_id, role in (("room_admin", "admin"), ("viewer", "viewer")):
		room = cmul8_routes.add_room_member(
			"project_api", cmul8_routes.RoomMemberBody(member_id=member_id, role=role, expected_revision=room["revision"]), request, owner,
		)

	# Owners and room admins retain the legacy product flow with only basic
	# tenant project access; viewers and nonmembers cannot reach its approval
	# or build side effects.
	api_main._require_room_graph_authority("project_api", _room_context("user_owner"))
	api_main._require_room_graph_authority("project_api", _room_context("room_admin"))
	for ctx in (_room_context("viewer"), _room_context("nonmember")):
		with pytest.raises(HTTPException) as denied_approve:
			api_main.post_approve("project_api", request, ctx)
		assert denied_approve.value.status_code == 410
		with pytest.raises(HTTPException) as denied_build:
			api_main.post_build("project_api", ctx)
		assert denied_build.value.status_code == 403


def test_project_bootstrap_creates_an_owner_room_before_initial_plan(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/projects"))
	state = SimpleNamespace(id="project_bootstrap", tenant_id="tenant_api")
	created: list[str] = []
	monkeypatch.setattr(api_main, "create_project", lambda *_args, **_kwargs: created.append("project") or state)
	monkeypatch.setattr(api_main, "audit_request", lambda *_args, **_kwargs: None)
	monkeypatch.setattr(api_main, "project_snapshot", lambda project_id: {"project": {"id": project_id, "phase": "plan", "plan_preview": {}, "prime": {"session_id": "s_1", "model": "private"}, "sandbox": {"runtime": "private"}}, "preview_data": {"columns": [], "rows": [], "row_count": 0}, "preview_url": None})

	def initial_plan(initial_state, *, actor_id):
		room = cmul8_routes.JsonCollaborationRepository(tmp_path / "control").get_room(
			initial_state.tenant_id, initial_state.id,
		)
		assert [(member.actor_id, member.role) for member in room.members] == [(actor_id, "owner")]
		assert room.members[0].display_name == "Owner"
		created.append("plan")
		return initial_state

	monkeypatch.setattr(api_main, "init_plan", initial_plan)
	body = api_main.CreateProjectBody(prompt="Create a controlled project")
	with pytest.raises(HTTPException) as denied:
		api_main.post_project(body, request, _member_context())
	assert denied.value.status_code == 403
	assert created == []

	result = api_main.post_project(body, request, _context())
	assert result["project"]["id"] == "project_bootstrap"
	assert "session_id" not in str(result).lower() and "sandbox" not in str(result).lower()
	assert created == ["project", "plan"]


def test_project_bootstrap_hides_storage_paths(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/projects"))
	secret_path = "/app/runs/proj_secret"
	monkeypatch.setattr(
		api_main,
		"create_project",
		lambda *_args, **_kwargs: (_ for _ in ()).throw(
			PermissionError(13, "Permission denied", secret_path),
		),
	)
	body = api_main.CreateProjectBody(prompt="Create a project")
	with pytest.raises(HTTPException) as unavailable:
		api_main.post_project(body, request, _context())
	assert unavailable.value.status_code == 503
	assert unavailable.value.detail == "Project storage is temporarily unavailable. Please try again."
	assert secret_path not in unavailable.value.detail


def test_public_project_audit_and_export_are_safe_evidence_only(monkeypatch, tmp_path):
	ctx = _context()
	monkeypatch.setattr(api_main, "project_snapshot", lambda _project_id: {
		"project": {"id": "project_api", "goal": "Review cases", "phase": "plan", "status": "ready", "sandbox": {"runtime": "private"}, "prime": {"model": "private", "session_id": "secret"}},
		"preview_data": {"columns": [], "rows": [], "row_count": 0}, "preview_url": None,
	})
	monkeypatch.setattr(api_main, "list_events", lambda _project_id: [{"id": "event_1", "ts": "now", "type": "tool", "label": "private", "detail": "args=secret", "status": "running"}])
	monkeypatch.setattr(api_main, "list_checkpoints", lambda _project_id: [{"id": "checkpoint_1", "label": "Reviewed", "raw_label": "private path", "current": True, "created_at": "now", "has_files": True}])
	monkeypatch.setattr(api_main, "_mission_root", tmp_path / "missions")
	mission_service = MissionService(JsonMissionRepository(api_main._mission_root))
	mission_service.bootstrap("tenant_api", "project_api", "user_owner", {"title": "Review cases"})
	mission_service.create_deliverable(
		"tenant_api", "project_api", {"type": "report", "name": "Brief", "source_ref": "internal/source", "artifact_ref": "internal/output"}, "user_owner", b"exact-private-bytes",
	)
	monkeypatch.setattr(api_main, "load_state", lambda _project_id: SimpleNamespace(tenant_id="tenant_api"))
	audit = api_main.project_audit("project_api", ctx)
	assert set(audit) == {"project", "events", "checkpoints", "deliverables"}
	serialized = json.dumps(audit).lower()
	forbidden = ("sandbox", "runtime", "prime", "model", "session_id", "raw_label", "args", "content_hash", "source_ref", "artifact_ref", "exact-private-bytes")
	assert not any(token in serialized for token in forbidden)
	response = api_main.export_audit("project_api", ctx)
	with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
		assert set(archive.namelist()) == {"audit.json", "events.json", "deliverables.json"}
		content = archive.read("audit.json").decode().lower()
		assert not any(token in content for token in forbidden)


def test_main_mutation_endpoints_carry_room_authority_and_preserve_read_only_chat(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	owner, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	for member_id, role in (("room_admin", "admin"), ("viewer", "viewer")):
		room = cmul8_routes.add_room_member(
			"project_api", cmul8_routes.RoomMemberBody(member_id=member_id, role=role, expected_revision=room["revision"]), request, owner,
		)

	marker = tmp_path / "project_api" / "app-source.txt"
	marker.write_text("unchanged")
	chat_actors: list[str | None] = []
	monkeypatch.setattr(
		api_main, "start_follow_up",
		lambda _project_id, _message, *, chat_id=None, actor_id=None: chat_actors.append(actor_id) or {"status": "running"},
	)
	for ctx in (_room_context("user_owner"), _room_context("room_admin"), _room_context("viewer"), _room_context("nonmember")):
		assert api_main.post_plan("project_api", api_main.ChatBody(message="Discuss the brief"), ctx)["status"] == "running"
		assert api_main.post_chat("project_api", api_main.ChatBody(message="Discuss the brief"), ctx)["status"] == "running"
	assert chat_actors == ["user_owner", "user_owner", "room_admin", "room_admin", "viewer", "viewer", "nonmember", "nonmember"]

	for ctx in (_room_context("viewer"), _room_context("nonmember")):
		with pytest.raises(HTTPException) as denied_approve:
			api_main.post_approve("project_api", request, ctx)
		assert denied_approve.value.status_code == 410
		with pytest.raises(HTTPException) as denied_build:
			api_main.post_build("project_api", ctx)
		assert denied_build.value.status_code == 403
	assert marker.read_text() == "unchanged"

	state = SimpleNamespace(tenant_id="tenant_api")
	class Store:
		def __init__(self, *_args, **_kwargs):
			pass
		def current_revision(self):
			return SimpleNamespace(revision_hash="approved-revision")
		def require_approved_revision(self, _revision_hash):
			return object()
	monkeypatch.setattr(api_main, "load_state", lambda _project_id: state)
	monkeypatch.setattr(api_main, "OperationGraphStore", Store)
	monkeypatch.setattr(api_main, "approved_graph_path", lambda _state: tmp_path / "approved.json")
	monkeypatch.setattr(api_main, "audit_request", lambda *_args, **_kwargs: None)
	approved_by: list[str | None] = []
	built_by: list[str | None] = []
	monkeypatch.setattr(
		api_main, "start_approve_build",
		lambda _project_id, *, actor_id=None: approved_by.append(actor_id) or {"job_id": "job_build", "status": "running"},
	)
	monkeypatch.setattr(api_main, "build_project", lambda _state, *, actor_id=None: built_by.append(actor_id))
	monkeypatch.setattr(api_main, "project_snapshot", lambda _project_id: {"id": "project_api"})
	for ctx in (_room_context("user_owner"), _room_context("room_admin")):
		with pytest.raises(HTTPException) as retired:
			api_main.post_approve("project_api", request, ctx)
		assert retired.value.status_code == 410
		assert api_main.post_build("project_api", ctx)["id"] == "project_api"
	assert approved_by == []
	assert built_by == ["user_owner", "room_admin"]
	monkeypatch.setattr(api_main, "approved_graph_path", lambda _state: (_ for _ in ()).throw(api_main.OperationGraphError("revision_hash=secret")))
	with pytest.raises(HTTPException) as unavailable:
		api_main.post_build("project_api", _room_context("user_owner"))
	assert unavailable.value.status_code == 409
	assert unavailable.value.detail == "The Mission plan is not ready to start. Review it and try again."


def test_observability_is_project_and_tenant_scoped(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	body = cmul8_routes.TelemetryEventBody(
		id="evt_api_1", entity_kind="workflow", entity_id="wf_case", entity_name="Case resolution",
		signal="workflow.completed", status="succeeded", started_at="2026-08-23T10:00:00+00:00",
		duration_ms=125,
	)
	cmul8_routes.ingest_telemetry("project_api", body, request, ctx)
	other = body.model_copy(update={"id": "evt_api_2"})
	cmul8_routes.ingest_telemetry("project_other", other, request, ctx)
	payload = cmul8_routes.get_observability("project_api", ctx)
	assert payload["overview"]["runs"] == 1
	assert payload["inventories"]["workflow"][0]["id"] == "wf_case"
	detail = cmul8_routes.get_observability_detail("project_api", "workflow", "wf_case", ctx)
	assert set(detail) == {"item", "recent_events"}
	assert set(detail["recent_events"][0]) <= {"id", "entity_kind", "entity_id", "entity_name", "signal", "status", "started_at", "duration_ms"}
	assert "environment" not in json.dumps(detail).lower()

	with pytest.raises(ValidationError):
		cmul8_routes.TelemetryEventBody(**body.model_dump(), trace_id="trace_api_1")
	with pytest.raises(ValidationError):
		cmul8_routes.TelemetryEventBody(**body.model_dump(), attributes={"auth": {"token": "raw"}})

	# Internal telemetry can retain correlation for its own store, while the
	# public detail is a deliberately separate DTO.
	cmul8_routes.JsonlTelemetryRepository(cmul8_routes._telemetry_root).append(cmul8_routes.TelemetryEvent(
		id="evt_internal", tenant_id="tenant_api", entity_kind=cmul8_routes.EntityKind.WORKFLOW, entity_id="wf_case",
		entity_name="Case resolution", signal="workflow.completed", status=cmul8_routes.EventStatus.SUCCEEDED,
		started_at="2026-08-23T10:01:00+00:00", trace_id="trace_secret", application_id="project_api",
		environment="private", message="private provider response", tags=("internal",),
		attributes={"tool": {"args": "secret"}},
	))
	public_detail = cmul8_routes.get_observability_detail("project_api", "workflow", "wf_case", ctx)
	forbidden = ("trace", "environment", "message", "tags", "attributes", "provider", "response", "args")
	assert not any(token in json.dumps(public_detail).lower() for token in forbidden)


def test_public_control_plane_bodies_reject_execution_fields(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	with pytest.raises(ValidationError):
		cmul8_routes.RuntimeJobBody(environment_id="private", payload={"tool": "secret"})
	with pytest.raises(ValidationError):
		cmul8_routes.TaskTransitionBody(state="working", expected_revision=1, result={"provider": "private"})
	with pytest.raises(ValidationError):
		cmul8_routes.CommentCreateBody(body="Review this", graph_path="/hidden")
	assert "result" not in cmul8_routes._public_task({"id": "task_1", "title": "Review", "result": {"provider": "private"}, "activity": [{"tool": "secret"}]})
	assert "activity" not in cmul8_routes._public_task({"id": "task_1", "title": "Review", "result": {"provider": "private"}, "activity": [{"tool": "secret"}]})
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.enqueue_runtime_job("project_api", cmul8_routes.RuntimeJobBody(), request, ctx)
	assert denied.value.status_code == 410


def _real_assigned_task(monkeypatch, tmp_path):
	project_root = _prepare(monkeypatch, tmp_path)
	monkeypatch.setattr(cmul8_routes, "_mission_root", tmp_path / "missions")
	monkeypatch.setattr(cmul8_routes, "RUNS_DIR", tmp_path / "runs")
	owner, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)
	repository, rooms = cmul8_routes._collaboration()
	room = repository.get_room("tenant_api", "project_api")
	rooms.add_member(
		tenant_id="tenant_api", project_id="project_api", actor_id="user_owner", member_id="user_reviewer",
		role="reviewer", member_name="Reviewer", expected_revision=room.revision,
	)
	mission = MissionService(JsonMissionRepository(tmp_path / "missions"))
	mission.bootstrap("tenant_api", "project_api", "user_owner", {"title": "Mission", "objective": "Reviewable work"})
	agent = mission.add_agent("tenant_api", "project_api", {"name": "Analyst", "role": "Finance", "mandate": "Prepare evidence"})
	raw = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	raw["metadata"].update({"tenant_id": "tenant_api", "project_id": "project_api"})
	graph = cmul8_routes.OperationGraphStore(project_root, tenant_id="tenant_api", project_id="project_api")
	revision = graph.create_revision(raw, expected_revision_hash=None)
	graph.approve_revision(revision.revision_hash, actor_id="user_owner")
	coordinator = cmul8_routes._assignment_coordinator(repository, "project_api")
	result = coordinator.assign(
		tenant_id="tenant_api", project_id="project_api", authenticated_human_actor_id="user_owner",
		client_request_id="real_assignment", body="Prepare the review pack.", title="Prepare review pack",
		objective="Prepare evidence for human review.", acceptance_criteria=["Evidence is reviewable"],
		assigned_agent_ids=[agent.id], reviewer_human_ids=["user_reviewer"], graph_revision=revision.revision_hash,
	)
	journal = next((tmp_path / "runs").glob(".workplace-control/tenant_api/project_api/assignment-transactions/*/conversation_assignment/*.json"))
	return owner, _room_context("user_reviewer"), request, repository, coordinator, result, journal


@pytest.mark.parametrize("admission_state", ["PREPARED", "COMMIT_DECIDED", "STORES_DURABLE"])
def test_real_incomplete_assigned_tasks_are_hidden_and_reject_every_public_mutation(monkeypatch, tmp_path, admission_state):
	owner, _reviewer, request, repository, _coordinator, result, journal = _real_assigned_task(monkeypatch, tmp_path)
	row = json.loads(journal.read_text()); row["state"] = admission_state; journal.write_text(json.dumps(row))
	legacy = repository.create_task(Task(
		id=f"task_legacy_{admission_state}", tenant_id="tenant_api", project_id="project_api", title="Legacy", objective="Keep visible",
		acceptance_criteria=["Visible"], state=TaskState.PROPOSED,
	))
	payload = cmul8_routes.get_room("project_api", owner)
	assert [task["id"] for task in payload["tasks"]] == [legacy.id]
	for operation in (
		lambda: cmul8_routes.transition_task(
			"project_api", result.task_id,
			cmul8_routes.TaskTransitionBody(state=TaskState.READY, expected_revision=1), request, owner,
		),
		lambda: cmul8_routes.claim_task("project_api", result.task_id, 1, request, owner),
		lambda: cmul8_routes.review_task(
			"project_api", result.task_id,
			cmul8_routes.TaskReviewBody(decision=ReviewDecision.APPROVE, expected_revision=1), request, owner,
		),
	):
		with pytest.raises(HTTPException) as blocked:
			operation()
		assert blocked.value.status_code == 409
		assert blocked.value.detail == cmul8_routes._ASSIGNMENT_TASK_UNAVAILABLE
	assert cmul8_routes.claim_task("project_api", legacy.id, 1, request, owner)["id"] == legacy.id


def test_real_complete_assigned_task_stays_visible_through_claim_transition_and_review(monkeypatch, tmp_path):
	owner, reviewer, request, _repository, _coordinator, result, _journal = _real_assigned_task(monkeypatch, tmp_path)
	assert [task["id"] for task in cmul8_routes.get_room("project_api", owner)["tasks"]] == [result.task_id]
	claimed = cmul8_routes.claim_task("project_api", result.task_id, 1, request, owner)
	assert claimed["owner_id"] == "user_owner"
	assert [task["id"] for task in cmul8_routes.get_room("project_api", owner)["tasks"]] == [result.task_id]
	working = cmul8_routes.transition_task(
		"project_api", result.task_id,
		cmul8_routes.TaskTransitionBody(state=TaskState.WORKING, expected_revision=claimed["revision"]), request, owner,
	)
	in_review = cmul8_routes.transition_task(
		"project_api", result.task_id,
		cmul8_routes.TaskTransitionBody(state=TaskState.IN_REVIEW, expected_revision=working["revision"]), request, owner,
	)
	assert [task["id"] for task in cmul8_routes.get_room("project_api", owner)["tasks"]] == [result.task_id]
	reviewed = cmul8_routes.review_task(
		"project_api", result.task_id,
		cmul8_routes.TaskReviewBody(decision=ReviewDecision.APPROVE, expected_revision=in_review["revision"]), request, reviewer,
	)
	assert reviewed["task"]["state"] == "done"
	assert [task["id"] for task in cmul8_routes.get_room("project_api", owner)["tasks"]] == [result.task_id]


def test_real_complete_assignment_hides_corrupted_immutable_task_link(monkeypatch, tmp_path):
	owner, _reviewer, request, repository, _coordinator, result, _journal = _real_assigned_task(monkeypatch, tmp_path)
	task = repository.get_task("tenant_api", "project_api", result.task_id)
	from dataclasses import replace
	repository.save_task(replace(task, source_message_id="msg_tampered", revision=task.revision + 1), task.revision)
	assert cmul8_routes.get_room("project_api", owner)["tasks"] == []
	with pytest.raises(HTTPException) as blocked:
		cmul8_routes.claim_task("project_api", result.task_id, 2, request, owner)
	assert blocked.value.detail == cmul8_routes._ASSIGNMENT_TASK_UNAVAILABLE
