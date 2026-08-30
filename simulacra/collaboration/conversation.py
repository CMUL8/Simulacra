"""Durable, crash-atomic collaboration conversation commands.

Conversation mutations deliberately use only the repository's single
``conversation_state.json`` replacement.  This module does not coordinate a
Mission-store mutation; assignment coordination is a later protocol.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Callable, Mapping

from .errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from .models import (
    CONVERSATION_REACTIONS,
    ConversationMessage,
    ConversationReaction,
    MessageAudit,
    SavedReference,
    new_id,
    validate_scope_id,
)
from .repository import CollaborationRepository


class ConversationConflictError(ConflictError):
    pass


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def canonical_body_hash(value: Mapping[str, Any]) -> str:
    """Hash the canonical public command body, never a Python repr."""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _screen_text(value: str) -> str:
    # Re-use the existing product's conservative secret boundary without
    # importing its service (which would create a dependency cycle).
    from simulacra.missions.service import _safe_value  # local import by design
    safe = _safe_value(value)
    if not isinstance(safe, str):
        raise ValidationError("invalid message body")
    return safe


def _public_scalar(value: Any) -> str | None:
    """A public nested value is always a screened scalar, never structure."""
    if not isinstance(value, str):
        return None
    return _screen_text(value)


def _public_link_value(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    try:
        validate_scope_id(value, "conversation_link_id")
    except ValidationError:
        return None
    return value


_PUBLIC_MESSAGE_KEYS = frozenset({
    "id", "mission_id", "kind", "author", "body", "created_at", "edited_at",
    "thread", "reactions", "saved", "links",
})


def _validated_reactions(
    reactions: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> list[dict[str, Any]]:
    if not isinstance(reactions, (list, tuple)):
        raise ValidationError("invalid conversation reactions")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in reactions:
        if not isinstance(item, Mapping) or set(item) != {"reaction", "count", "reacted"}:
            raise ValidationError("invalid conversation reaction projection")
        reaction, count, reacted = item.get("reaction"), item.get("count"), item.get("reacted")
        if (
            reaction not in CONVERSATION_REACTIONS
            or reaction in seen
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count < 1
            or not isinstance(reacted, bool)
        ):
            raise ValidationError("invalid conversation reaction projection")
        seen.add(reaction)
        result.append({"reaction": reaction, "count": count, "reacted": reacted})
    if [item["reaction"] for item in result] != [item for item in CONVERSATION_REACTIONS if item in seen]:
        raise ValidationError("invalid conversation reaction order")
    return result


def _validate_public_message_payload(row: Mapping[str, Any], *, nested: bool = False) -> None:
    if not isinstance(row, Mapping) or set(row) != _PUBLIC_MESSAGE_KEYS:
        raise ValidationError("invalid public conversation message")
    for key in ("id", "mission_id", "kind", "created_at"):
        if not isinstance(row.get(key), str) or not row[key]:
            raise ValidationError("invalid public conversation message")
    if row.get("body") is not None and not isinstance(row.get("body"), str):
        raise ValidationError("invalid public conversation message")
    if row.get("edited_at") is not None and not isinstance(row.get("edited_at"), str):
        raise ValidationError("invalid public conversation message")
    author = row.get("author")
    if (
        not isinstance(author, Mapping)
        or not set(author).issubset({"id", "kind", "display_name", "avatar_url"})
        or any(not isinstance(value, str) for value in author.values())
    ):
        raise ValidationError("invalid public conversation author")
    links = row.get("links")
    if (
        not isinstance(links, Mapping)
        or set(links) != {"work_item_id", "run_id", "output_id"}
        or any(value is not None and not isinstance(value, str) for value in links.values())
    ):
        raise ValidationError("invalid public conversation links")
    thread = row.get("thread")
    if not isinstance(thread, Mapping) or set(thread) != {"reply_count", "latest_replies"}:
        raise ValidationError("invalid conversation thread projection")
    reply_count, latest = thread.get("reply_count"), thread.get("latest_replies")
    if (
        isinstance(reply_count, bool)
        or not isinstance(reply_count, int)
        or reply_count < 0
        or not isinstance(latest, list)
        or len(latest) > 3
        or reply_count < len(latest)
    ):
        raise ValidationError("invalid conversation thread projection")
    if nested and (reply_count != 0 or latest):
        raise ValidationError("conversation reply depth must be one")
    for reply in latest:
        _validate_public_message_payload(reply, nested=True)
    _validated_reactions(row.get("reactions"))
    if not isinstance(row.get("saved"), bool):
        raise ValidationError("invalid saved conversation projection")


def serialize_conversation_message(
    message: ConversationMessage, *, thread: Mapping[str, Any] | None = None,
    reactions: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...] = (), saved: bool = False,
) -> dict[str, Any]:
    """Return the conversation's public, recursively allow-listed shape."""
    author = message.author if isinstance(message.author, dict) else {}
    links = message.links if isinstance(message.links, dict) else {}
    raw_thread = thread if thread is not None else {"reply_count": 0, "latest_replies": []}
    if not isinstance(raw_thread, Mapping) or set(raw_thread) != {"reply_count", "latest_replies"}:
        raise ValidationError("invalid conversation thread projection")
    latest = raw_thread.get("latest_replies")
    if not isinstance(latest, list):
        raise ValidationError("invalid conversation thread projection")
    row = {
        "id": message.id,
        "mission_id": message.project_id,
        "kind": message.kind,
        "author": {key: safe for key in ("id", "kind", "display_name", "avatar_url") if (safe := _public_scalar(author.get(key))) is not None},
        "body": _screen_text(message.body) if message.body is not None else None,
        "created_at": message.created_at,
        "edited_at": message.edited_at,
        "thread": {"reply_count": raw_thread.get("reply_count"), "latest_replies": list(latest)},
        "reactions": _validated_reactions(reactions),
        "saved": saved,
        "links": {key: _public_link_value(links.get(key)) for key in ("work_item_id", "run_id", "output_id")},
    }
    _validate_public_message_payload(row)
    return row


@dataclass(frozen=True, slots=True)
class ReactionMutationResult:
    message: ConversationMessage
    reactions: tuple[dict[str, Any], ...]
    thread: dict[str, Any]
    saved: bool


@dataclass(frozen=True, slots=True)
class SavedMutationResult:
    saved: bool


@dataclass(frozen=True, slots=True)
class ConversationMessageView:
    message: ConversationMessage
    thread: dict[str, Any]
    reactions: tuple[dict[str, Any], ...]
    saved: bool


class ConversationService:
    def __init__(self, repository: CollaborationRepository, *, clock: Callable[[], str] = _utc_now,
                 id_factory: Callable[[str], str] = new_id):
        self.repository = repository
        self._clock = clock
        self._id_factory = id_factory

    @staticmethod
    def _identity(tenant_id: str, project_id: str, actor_id: str, operation: str, client_request_id: str) -> str:
        for value, label in ((tenant_id, "tenant_id"), (project_id, "project_id"), (actor_id, "actor_id"), (client_request_id, "client_request_id")):
            validate_scope_id(value, label)
        if operation not in {
            "create", "edit", "delete", "conversation_reply",
            "conversation_reaction_put", "conversation_reaction_delete",
            "conversation_saved_put", "conversation_saved_delete",
        }:
            raise ValidationError("invalid conversation operation")
        return "|".join((tenant_id, project_id, actor_id, operation, client_request_id))

    _MUTATION_ROLES = frozenset({"owner", "admin", "member", "reviewer", "approver"})

    def _require_human_member(self, tenant_id: str, project_id: str, actor_id: str) -> None:
        room = self.repository.get_room(tenant_id, project_id)
        member = self.repository.visible_member(room, actor_id)
        if member is None or member.role not in self._MUTATION_ROLES:
            raise AuthorizationError("actor is not a project room member")

    def _require_human_member_locked(self, tenant_id: str, project_id: str, actor_id: str) -> None:
        """Authoritative membership check while the project write lock is held."""
        room = self.repository.get_room(tenant_id, project_id)
        member = self.repository.visible_member(room, actor_id)
        if member is None or member.role not in self._MUTATION_ROLES:
            raise AuthorizationError("actor is not a project room member")

    def _require_current_member(self, tenant_id: str, project_id: str, actor_id: str) -> None:
        room = self.repository.get_room(tenant_id, project_id)
        if self.repository.visible_member(room, actor_id) is None:
            raise AuthorizationError("actor is not a project room member")

    def _append_wake_event(
        self, state: dict[str, Any], *, project_id: str, event_type: str,
        occurred_at: str, recipient_human_id: str | None = None,
    ) -> None:
        event_id = self._id_factory("evt")
        while event_id in state["wake_events"]:
            event_id = self._id_factory("evt")
        state["wake_events"][event_id] = {
            "id": event_id,
            "type": event_type,
            "mission_id": project_id,
            "occurred_at": occurred_at,
            "recipient_human_id": recipient_human_id,
        }

    def project_agent_completion(
        self, *, tenant_id: str, project_id: str, source_event_id: str,
        agent_id: str, body: str, created_at: str,
        work_item_id: str, run_id: str, output_id: str | None,
    ) -> ConversationMessage:
        """Project one durable Mission result into the shared conversation.

        The Mission trajectory is the source record. A deterministic message
        and wake-up identity make a worker retry or restart converge on the
        same visible contribution instead of creating duplicate chat rows.
        """
        return self._project_agent_event(
            tenant_id=tenant_id, project_id=project_id, source_event_id=source_event_id,
            agent_id=agent_id, kind="agent_completed", body=body, created_at=created_at,
            work_item_id=work_item_id, run_id=run_id, output_id=output_id,
        )

    def project_agent_progress(
        self, *, tenant_id: str, project_id: str, source_event_id: str,
        agent_id: str, body: str, created_at: str,
        work_item_id: str, run_id: str,
    ) -> ConversationMessage:
        """Project a durable, product-safe agent progress milestone."""
        return self._project_agent_event(
            tenant_id=tenant_id, project_id=project_id, source_event_id=source_event_id,
            agent_id=agent_id, kind="agent_started", body=body, created_at=created_at,
            work_item_id=work_item_id, run_id=run_id, output_id=None,
        )

    def project_agent_failure(
        self, *, tenant_id: str, project_id: str, source_event_id: str,
        agent_id: str, body: str, created_at: str,
        work_item_id: str, run_id: str,
    ) -> ConversationMessage:
        """Project a durable, product-safe stopped-work milestone."""
        return self._project_agent_event(
            tenant_id=tenant_id, project_id=project_id, source_event_id=source_event_id,
            agent_id=agent_id, kind="agent_progress", body=body, created_at=created_at,
            work_item_id=work_item_id, run_id=run_id, output_id=None,
        )

    def _project_agent_event(
        self, *, tenant_id: str, project_id: str, source_event_id: str,
        agent_id: str, kind: str, body: str, created_at: str,
        work_item_id: str, run_id: str, output_id: str | None,
    ) -> ConversationMessage:
        if kind not in {"agent_started", "agent_progress", "agent_completed"}:
            raise ConversationConflictError("agent_result_projection_conflict")
        for value, label in (
            (tenant_id, "tenant_id"), (project_id, "project_id"),
            (source_event_id, "source_event_id"), (agent_id, "agent_id"),
            (work_item_id, "work_item_id"), (run_id, "run_id"),
        ):
            validate_scope_id(value, label)
        if output_id is not None:
            validate_scope_id(output_id, "output_id")
        safe_body = _screen_text(body)
        digest = hashlib.sha256(
            "\0".join((tenant_id, project_id, source_event_id)).encode("utf-8"),
        ).hexdigest()[:32]
        message = ConversationMessage(
            id=f"message_agent_{digest}", tenant_id=tenant_id, project_id=project_id,
            author={"id": agent_id, "kind": "agent"}, kind=kind,
            body=safe_body, created_at=created_at,
            links={"work_item_id": work_item_id, "run_id": run_id, "output_id": output_id},
        )
        wake_id = f"evt_agent_{digest}"

        def mutate(state: dict[str, Any]) -> None:
            existing = state["messages"].get(message.id)
            if existing is None:
                state["messages"][message.id] = message.to_dict()
            elif existing != message.to_dict():
                raise ConversationConflictError("agent_result_projection_conflict")
            wake = {
                "id": wake_id, "type": "conversation.changed", "mission_id": project_id,
                "occurred_at": created_at, "recipient_human_id": None,
            }
            existing_wake = state["wake_events"].get(wake_id)
            if existing_wake is None:
                state["wake_events"][wake_id] = wake
            elif existing_wake != wake:
                raise ConversationConflictError("agent_result_projection_conflict")

        self.repository.mutate_conversation_state(tenant_id, project_id, mutate)
        return message

    @staticmethod
    def _reaction_key(message_id: str, actor_id: str, reaction: str) -> str:
        return "|".join((message_id, actor_id, reaction))

    @staticmethod
    def _saved_key(human_id: str, message_id: str) -> str:
        return "|".join((human_id, "conversation_message", message_id))

    @staticmethod
    def _message_from_state(state: Mapping[str, Any], message_id: str, *, available: bool = True) -> ConversationMessage:
        row = state.get("messages", {}).get(message_id) if isinstance(state.get("messages"), Mapping) else None
        if not isinstance(row, Mapping):
            raise NotFoundError("conversation message not found")
        message = ConversationMessage.from_dict(row)
        if available and message.deleted_at is not None:
            raise NotFoundError("conversation message not found")
        return message

    def _resolve_message_locked(
        self, state: Mapping[str, Any], *, tenant_id: str, project_id: str,
        message_id: str, available: bool = True,
    ) -> ConversationMessage:
        try:
            return self._message_from_state(state, message_id, available=available)
        except NotFoundError:
            pass
        comment = next(
            (item for item in self.repository.list_comments(tenant_id, project_id) if item.id == message_id),
            None,
        )
        if comment is None:
            raise NotFoundError("conversation message not found")
        return ConversationMessage(
            id=comment.id, tenant_id=comment.tenant_id, project_id=comment.project_id,
            author={"id": comment.author_id, "kind": "human"}, kind="human_message",
            body=_screen_text(comment.body), created_at=comment.created_at, revision=comment.revision,
            links={"work_item_id": comment.task_id, "run_id": None, "output_id": None},
        )

    @staticmethod
    def _reaction_projection(state: Mapping[str, Any], message_id: str, viewer_id: str) -> tuple[dict[str, Any], ...]:
        counts: dict[str, int] = {reaction: 0 for reaction in CONVERSATION_REACTIONS}
        reacted: set[str] = set()
        raw_reactions = state.get("reactions")
        if not isinstance(raw_reactions, Mapping):
            raise ConversationConflictError("conversation_state_corrupt")
        for raw in raw_reactions.values():
            if not isinstance(raw, Mapping):
                raise ConversationConflictError("conversation_state_corrupt")
            try:
                item = ConversationReaction.from_dict(raw)
            except Exception as exc:
                raise ConversationConflictError("conversation_state_corrupt") from exc
            if item.to_dict() != dict(raw):
                raise ConversationConflictError("conversation_state_corrupt")
            if item.message_id != message_id:
                continue
            counts[item.reaction] += 1
            if item.actor_id == viewer_id:
                reacted.add(item.reaction)
        return tuple(
            {"reaction": reaction, "count": counts[reaction], "reacted": reaction in reacted}
            for reaction in CONVERSATION_REACTIONS if counts[reaction]
        )

    def _view_snapshot_locked(
        self, state: Mapping[str, Any], *, tenant_id: str, project_id: str,
        message: ConversationMessage, viewer_id: str,
    ) -> dict[str, Any]:
        rows: list[ConversationMessage] = []
        raw_messages = state.get("messages")
        if not isinstance(raw_messages, Mapping):
            raise ConversationConflictError("conversation_state_corrupt")
        try:
            for raw in raw_messages.values():
                if not isinstance(raw, Mapping):
                    raise ValueError("invalid message")
                item = ConversationMessage.from_dict(raw)
                if item.to_dict() != dict(raw):
                    raise ValueError("noncanonical message")
                rows.append(item)
        except Exception as exc:
            raise ConversationConflictError("conversation_state_corrupt") from exc
        replies = sorted(
            (item for item in rows if item.root_message_id == message.id),
            key=lambda item: (item.created_at, item.id),
        ) if message.root_message_id is None else []
        saved_key = self._saved_key(viewer_id, message.id)
        raw_saved = state.get("saved_references")
        if not isinstance(raw_saved, Mapping):
            raise ConversationConflictError("conversation_state_corrupt")
        saved = raw_saved.get(saved_key)
        if saved is not None:
            try:
                reference = SavedReference.from_dict(saved)
            except Exception as exc:
                raise ConversationConflictError("conversation_state_corrupt") from exc
            if not isinstance(saved, Mapping) or reference.to_dict() != dict(saved):
                raise ConversationConflictError("conversation_state_corrupt")
        return {
            "thread": {
                "reply_count": len(replies),
                "latest_replies": [item.to_dict() for item in replies[-3:]],
            },
            "reactions": list(self._reaction_projection(state, message.id, viewer_id)),
            "saved": saved is not None,
        }

    @staticmethod
    def _validated_view_snapshot(
        raw: Mapping[str, Any], *, tenant_id: str, project_id: str,
    ) -> tuple[dict[str, Any], tuple[dict[str, Any], ...], bool]:
        if not isinstance(raw, Mapping) or set(raw) != {"thread", "reactions", "saved"}:
            raise ConversationConflictError("idempotency_corrupt")
        thread = raw.get("thread")
        if not isinstance(thread, Mapping) or set(thread) != {"reply_count", "latest_replies"}:
            raise ConversationConflictError("idempotency_corrupt")
        reply_count, latest = thread.get("reply_count"), thread.get("latest_replies")
        if (
            isinstance(reply_count, bool)
            or not isinstance(reply_count, int)
            or reply_count < 0
            or not isinstance(latest, list)
            or len(latest) > 3
            or reply_count < len(latest)
            or not isinstance(raw.get("saved"), bool)
        ):
            raise ConversationConflictError("idempotency_corrupt")
        replies: list[ConversationMessage] = []
        try:
            for item in latest:
                if not isinstance(item, Mapping):
                    raise ValueError("invalid reply")
                reply = ConversationMessage.from_dict(item)
                if (
                    reply.to_dict() != dict(item)
                    or reply.tenant_id != tenant_id
                    or reply.project_id != project_id
                    or reply.root_message_id is None
                ):
                    raise ValueError("noncanonical reply")
                replies.append(reply)
            reactions = tuple(_validated_reactions(raw.get("reactions")))
        except Exception as exc:
            raise ConversationConflictError("idempotency_corrupt") from exc
        return (
            {"reply_count": reply_count, "latest_replies": replies},
            reactions,
            bool(raw["saved"]),
        )

    @staticmethod
    def _reconstruct_legacy_response(state: dict[str, Any], prior: Mapping[str, Any], *, operation: str,
                                     tenant_id: str, project_id: str,
                                     authenticated_human_actor_id: str, client_request_id: str) -> ConversationMessage:
        """Validate the complete canonical history and derive one exact response."""
        response_ref = prior.get("response_ref")
        message_id = response_ref.get("message_id") if isinstance(response_ref, Mapping) else None
        row = state["messages"].get(message_id)
        if not isinstance(message_id, str) or not isinstance(row, dict):
            raise ConversationConflictError("idempotency_corrupt")
        try:
            current = ConversationMessage.from_dict(row)
        except Exception as exc:
            raise ConversationConflictError("idempotency_corrupt") from exc
        if row != current.to_dict():
            raise ConversationConflictError("idempotency_corrupt")
        author = current.author
        if (current.id != message_id or current.tenant_id != tenant_id or current.project_id != project_id
                or not isinstance(author, Mapping) or author.get("id") != authenticated_human_actor_id
                or author.get("kind") != "human"):
            raise ConversationConflictError("idempotency_corrupt")
        raw_audits = state.get("message_audits")
        raw_records = state.get("idempotency")
        if not isinstance(raw_audits, dict) or not isinstance(raw_records, dict):
            raise ConversationConflictError("idempotency_corrupt")
        try:
            audits: list[MessageAudit] = []
            for raw in raw_audits.values():
                if not isinstance(raw, dict):
                    raise ValueError("invalid audit")
                audit = MessageAudit.from_dict(raw)
                if raw != audit.to_dict():
                    raise ValueError("noncanonical audit")
                if audit.message_id == message_id:
                    audits.append(audit)
        except Exception as exc:
            raise ConversationConflictError("idempotency_corrupt") from exc
        chain = sorted(audits, key=lambda item: (item.prior_revision, item.occurred_at, item.id))
        expected_prior, tombstoned = 1, False
        for audit in chain:
            if (tombstoned or audit.actor_id != authenticated_human_actor_id
                    or audit.prior_revision != expected_prior
                    or audit.resulting_revision != audit.prior_revision + 1
                    or not isinstance(audit.prior_body, str)):
                raise ConversationConflictError("idempotency_corrupt")
            tombstoned = tombstoned or audit.operation == "delete"
            expected_prior = audit.resulting_revision

        linked: dict[tuple[str, str, str], dict[str, Any]] = {}
        fields = {"operation", "authenticated_human_actor_id", "client_request_id", "canonical_body_hash", "response_ref", "created_at"}
        try:
            for identity_key, record in raw_records.items():
                if not isinstance(identity_key, str) or not isinstance(record, dict) or set(record) != fields:
                    raise ValueError("noncanonical idempotency record")
                op, actor_id, request_id = record["operation"], record["authenticated_human_actor_id"], record["client_request_id"]
                if (op not in {
                        "create", "edit", "delete", "conversation_reply",
                        "conversation_reaction_put", "conversation_reaction_delete",
                        "conversation_saved_put", "conversation_saved_delete",
                    } or not isinstance(actor_id, str)
                        or not isinstance(request_id, str) or not isinstance(record["created_at"], str)
                        or not isinstance(record["canonical_body_hash"], str)
                        or len(record["canonical_body_hash"]) != 64
                        or any(char not in "0123456789abcdef" for char in record["canonical_body_hash"])):
                    raise ValueError("invalid idempotency metadata")
                if identity_key != ConversationService._identity(tenant_id, project_id, actor_id, op, request_id):
                    raise ValueError("idempotency identity mismatch")
                ref = record["response_ref"]
                if not isinstance(ref, dict) or "message_id" not in ref or "response_snapshot" not in ref:
                    if op in {"create", "edit", "delete"} and isinstance(ref, dict) and set(ref) == {"message_id"}:
                        pass
                    else:
                        raise ValueError("invalid response reference")
                allowed_ref = {"message_id", "response_snapshot"}
                if op == "conversation_reply":
                    allowed_ref.add("parent_message_id")
                    allowed_ref.add("public_snapshot")
                legacy_reply_ref = allowed_ref - {"public_snapshot"}
                if set(ref) not in (allowed_ref, legacy_reply_ref, {"message_id"}):
                    raise ValueError("invalid response reference")
                ref_message_id = ref.get("message_id")
                validate_scope_id(ref_message_id, "message_id")
                if op not in {"create", "edit", "delete", "conversation_reply"}:
                    # Reaction/save records share the idempotency bucket but do
                    # not participate in the editable message audit chain.
                    continue
                if ref_message_id != message_id:
                    continue
                if actor_id != authenticated_human_actor_id:
                    raise ValueError("message response actor mismatch")
                key = (op, actor_id, request_id)
                if key in linked:
                    raise ValueError("duplicate message response")
                linked[key] = record
        except Exception as exc:
            raise ConversationConflictError("idempotency_corrupt") from exc

        original_body = chain[0].prior_body if chain else current.body
        if not isinstance(original_body, str):
            raise ConversationConflictError("idempotency_corrupt")
        original = replace(current, body=original_body, revision=1, edited_at=None, deleted_at=None)
        expected_responses: dict[tuple[str, str, str], ConversationMessage] = {}
        origin_records = [key for key in linked if key[0] in {"create", "conversation_reply"}]
        if len(origin_records) != 1:
            raise ConversationConflictError("idempotency_corrupt")
        create_key = origin_records[0]
        create_record = linked[create_key]
        if create_key[0] == "create":
            origin_hash = canonical_body_hash({
                "body": original.body, "links": original.links,
                "root_message_id": original.root_message_id, "source_message_id": original.source_message_id,
            })
        else:
            ref = create_record["response_ref"]
            parent_id = ref.get("parent_message_id") if isinstance(ref, Mapping) else None
            try:
                validate_scope_id(parent_id, "parent_message_id")
            except ValidationError as exc:
                raise ConversationConflictError("idempotency_corrupt") from exc
            if original.root_message_id is None:
                raise ConversationConflictError("idempotency_corrupt")
            origin_hash = canonical_body_hash({
                "method": "POST", "parent_message_id": parent_id,
                "body": {"client_request_id": create_key[2], "body": original.body},
            })
        if create_record["created_at"] != original.created_at or create_record["canonical_body_hash"] != origin_hash:
            raise ConversationConflictError("idempotency_corrupt")
        expected_responses[create_key] = original
        result = original
        for index, audit in enumerate(chain):
            if audit.prior_body != result.body:
                raise ConversationConflictError("idempotency_corrupt")
            key = (audit.operation, audit.actor_id, audit.client_request_id)
            record = linked.get(key)
            if record is None or record["created_at"] != audit.occurred_at:
                raise ConversationConflictError("idempotency_corrupt")
            if audit.operation == "edit":
                body = chain[index + 1].prior_body if index + 1 < len(chain) else current.body
                if not isinstance(body, str):
                    raise ConversationConflictError("idempotency_corrupt")
                result = replace(result, body=body, revision=audit.resulting_revision,
                                 edited_at=audit.occurred_at, deleted_at=None)
            else:
                result = replace(result, body=None, revision=audit.resulting_revision,
                                 edited_at=audit.occurred_at, deleted_at=audit.occurred_at)
            expected_hash = canonical_body_hash({
                "message_id": message_id, "expected_revision": audit.prior_revision,
                "body": result.body if audit.operation == "edit" else None,
            })
            if record["canonical_body_hash"] != expected_hash:
                raise ConversationConflictError("idempotency_corrupt")
            expected_responses[key] = result
        if result.to_dict() != current.to_dict() or len(linked) != len(chain) + 1:
            raise ConversationConflictError("idempotency_corrupt")
        for key, record in linked.items():
            expected = expected_responses.get(key)
            if expected is None:
                raise ConversationConflictError("idempotency_corrupt")
            ref = record["response_ref"]
            if "response_snapshot" not in ref:
                continue
            raw_snapshot = ref["response_snapshot"]
            if not isinstance(raw_snapshot, dict):
                raise ConversationConflictError("idempotency_corrupt")
            try:
                snapshot = ConversationMessage.from_dict(raw_snapshot)
            except Exception as exc:
                raise ConversationConflictError("idempotency_corrupt") from exc
            if raw_snapshot != snapshot.to_dict() or snapshot.to_dict() != expected.to_dict():
                raise ConversationConflictError("idempotency_corrupt")
        requested = expected_responses.get((operation, authenticated_human_actor_id, client_request_id))
        if requested is None:
            raise ConversationConflictError("idempotency_corrupt")
        return requested

    @staticmethod
    def _replay(state: dict[str, Any], identity: str, body_hash: str, *, tenant_id: str, project_id: str,
                operation: str, authenticated_human_actor_id: str, client_request_id: str) -> ConversationMessage | None:
        prior = state["idempotency"].get(identity)
        if prior is None:
            return None
        if not isinstance(prior, dict) or prior.get("canonical_body_hash") != body_hash:
            raise ConversationConflictError("idempotency_mismatch")
        if (prior.get("operation") != operation
                or prior.get("authenticated_human_actor_id") != authenticated_human_actor_id
                or prior.get("client_request_id") != client_request_id):
            raise ConversationConflictError("idempotency_corrupt")
        response_ref = prior.get("response_ref")
        message_id = response_ref.get("message_id") if isinstance(response_ref, Mapping) else None
        try:
            if not isinstance(message_id, str):
                raise ValidationError("invalid response message id")
            validate_scope_id(message_id, "message_id")
        except ValidationError as exc:
            raise ConversationConflictError("idempotency_corrupt") from exc
        snapshot = response_ref.get("response_snapshot") if isinstance(response_ref, dict) else None
        derived = ConversationService._reconstruct_legacy_response(
            state, prior, operation=operation, tenant_id=tenant_id, project_id=project_id,
            authenticated_human_actor_id=authenticated_human_actor_id,
            client_request_id=client_request_id,
        )
        if snapshot is not None:
            if not isinstance(snapshot, dict):
                raise ConversationConflictError("idempotency_corrupt")
            try:
                replay = ConversationMessage.from_dict(snapshot)
            except Exception as exc:
                raise ConversationConflictError("idempotency_corrupt") from exc
            if (replay.id != message_id or replay.tenant_id != tenant_id or replay.project_id != project_id
                    or not isinstance(replay.author, Mapping) or replay.author.get("id") != authenticated_human_actor_id
                    or replay.author.get("kind") != "human" or replay.to_dict() != derived.to_dict()):
                raise ConversationConflictError("idempotency_corrupt")
            return replay
        # Expand-only compatibility with W1 records written before response
        # snapshots existed; exact replay is still reconstructed and verified.
        return derived

    @staticmethod
    def _record_idempotency(state: dict[str, Any], identity: str, body_hash: str, response: ConversationMessage,
                            *, operation: str, authenticated_human_actor_id: str,
                            client_request_id: str, created_at: str) -> None:
        state["idempotency"][identity] = {
            "operation": operation,
            "authenticated_human_actor_id": authenticated_human_actor_id,
            "client_request_id": client_request_id,
            "canonical_body_hash": body_hash,
            "response_ref": {"message_id": response.id, "response_snapshot": response.to_dict()},
            "created_at": created_at,
        }

    @staticmethod
    def _generic_replay(
        state: dict[str, Any], identity: str, body_hash: str, *, operation: str,
        authenticated_human_actor_id: str, client_request_id: str,
        expected_message_id: str | None = None,
    ) -> Mapping[str, Any] | None:
        prior = state["idempotency"].get(identity)
        if prior is None:
            return None
        fields = {
            "operation", "authenticated_human_actor_id", "client_request_id",
            "canonical_body_hash", "response_ref", "created_at",
        }
        if not isinstance(prior, Mapping) or set(prior) != fields or prior.get("canonical_body_hash") != body_hash:
            raise ConversationConflictError("idempotency_mismatch")
        if (
            prior.get("operation") != operation
            or prior.get("authenticated_human_actor_id") != authenticated_human_actor_id
            or prior.get("client_request_id") != client_request_id
            or not isinstance(prior.get("created_at"), str)
        ):
            raise ConversationConflictError("idempotency_corrupt")
        response_ref = prior.get("response_ref")
        snapshot = response_ref.get("response_snapshot") if isinstance(response_ref, Mapping) else None
        if not isinstance(response_ref, Mapping) or not isinstance(snapshot, Mapping):
            raise ConversationConflictError("idempotency_corrupt")
        allowed_ref = {"message_id", "response_snapshot"}
        if operation == "conversation_reply":
            allowed_ref.update({"parent_message_id", "public_snapshot"})
        legacy_allowed_ref = allowed_ref - {"public_snapshot"}
        if set(response_ref) not in (allowed_ref, legacy_allowed_ref):
            raise ConversationConflictError("idempotency_corrupt")
        if expected_message_id is not None and response_ref.get("message_id") != expected_message_id:
            raise ConversationConflictError("idempotency_corrupt")
        return snapshot

    @staticmethod
    def _record_generic_idempotency(
        state: dict[str, Any], identity: str, body_hash: str, *, operation: str,
        authenticated_human_actor_id: str, client_request_id: str, created_at: str,
        message_id: str, response_snapshot: Mapping[str, Any], extra_ref: Mapping[str, Any] | None = None,
    ) -> None:
        state["idempotency"][identity] = {
            "operation": operation,
            "authenticated_human_actor_id": authenticated_human_actor_id,
            "client_request_id": client_request_id,
            "canonical_body_hash": body_hash,
            "response_ref": {
                "message_id": message_id,
                "response_snapshot": dict(response_snapshot),
                **dict(extra_ref or {}),
            },
            "created_at": created_at,
        }

    def create_message(self, *, tenant_id: str, project_id: str, authenticated_human_actor_id: str,
                       client_request_id: str, body: str, links: Mapping[str, Any] | None = None,
                       root_message_id: str | None = None, source_message_id: str | None = None) -> ConversationMessage:
        self._require_human_member(tenant_id, project_id, authenticated_human_actor_id)
        if not isinstance(body, str) or not body.strip():
            raise ValidationError("message body is required")
        clean_body = _screen_text(body.strip())
        if links is not None and not isinstance(links, Mapping):
            raise ValidationError("conversation links must be an object")
        clean_links: dict[str, str] = {}
        for key, value in dict(links or {}).items():
            if key not in {"work_item_id", "run_id", "output_id"}:
                continue
            if not isinstance(value, str):
                raise ValidationError("conversation links must contain scalar identifiers")
            validate_scope_id(value, f"{key}")
            clean_links[key] = value
        if root_message_id is not None:
            validate_scope_id(root_message_id, "root_message_id")
        if source_message_id is not None:
            validate_scope_id(source_message_id, "source_message_id")
        identity = self._identity(tenant_id, project_id, authenticated_human_actor_id, "create", client_request_id)
        body_hash = canonical_body_hash({"body": clean_body, "links": clean_links, "root_message_id": root_message_id, "source_message_id": source_message_id})

        def mutate(state: dict[str, Any]) -> ConversationMessage:
            self._require_human_member_locked(tenant_id, project_id, authenticated_human_actor_id)
            replay = self._replay(
                state, identity, body_hash, operation="create",
                tenant_id=tenant_id, project_id=project_id,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id,
            )
            if replay is not None:
                return replay
            if root_message_id is not None:
                root = state["messages"].get(root_message_id)
                if not isinstance(root, dict) or root.get("root_message_id") is not None:
                    raise ValidationError("thread replies must reference a root message")
            created = ConversationMessage(
                id=self._id_factory("msg"), tenant_id=tenant_id, project_id=project_id,
                author={"id": authenticated_human_actor_id, "kind": "human"}, kind="human_message",
                body=clean_body, created_at=self._clock(), root_message_id=root_message_id,
                source_message_id=source_message_id, links=clean_links,
            )
            state["messages"][created.id] = created.to_dict()
            self._record_idempotency(
                state, identity, body_hash, created, operation="create",
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id, created_at=created.created_at,
            )
            self._append_wake_event(
                state, project_id=project_id, event_type="conversation.changed", occurred_at=created.created_at,
            )
            return created

        return self.repository.mutate_conversation_state(tenant_id, project_id, mutate)

    def reply_message(
        self, *, tenant_id: str, project_id: str, authenticated_human_actor_id: str,
        parent_message_id: str, client_request_id: str, body: str,
    ) -> ConversationMessage:
        self._require_human_member(tenant_id, project_id, authenticated_human_actor_id)
        validate_scope_id(parent_message_id, "parent_message_id")
        if not isinstance(body, str) or not body.strip():
            raise ValidationError("message body is required")
        clean_body = _screen_text(body.strip())
        operation = "conversation_reply"
        identity = self._identity(
            tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id,
        )
        body_hash = canonical_body_hash({
            "method": "POST", "parent_message_id": parent_message_id,
            "body": {"client_request_id": client_request_id, "body": clean_body},
        })

        def mutate(state: dict[str, Any]) -> ConversationMessage:
            self._require_human_member_locked(tenant_id, project_id, authenticated_human_actor_id)
            snapshot = self._generic_replay(
                state, identity, body_hash, operation=operation,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id,
            )
            if snapshot is not None:
                try:
                    replay = ConversationMessage.from_dict(snapshot)
                except Exception as exc:
                    raise ConversationConflictError("idempotency_corrupt") from exc
                record = state["idempotency"][identity]
                if (
                    dict(snapshot) != replay.to_dict()
                    or replay.author.get("id") != authenticated_human_actor_id
                    or replay.tenant_id != tenant_id
                    or replay.project_id != project_id
                    or record["response_ref"].get("message_id") != replay.id
                ):
                    raise ConversationConflictError("idempotency_corrupt")
                return replay
            parent = self._resolve_message_locked(
                state, tenant_id=tenant_id, project_id=project_id, message_id=parent_message_id,
            )
            root_id = parent.root_message_id or parent.id
            root = self._resolve_message_locked(
                state, tenant_id=tenant_id, project_id=project_id, message_id=root_id,
            )
            if root.root_message_id is not None:
                raise ConversationConflictError("conversation_state_corrupt")
            stamp = self._clock()
            created = ConversationMessage(
                id=self._id_factory("msg"), tenant_id=tenant_id, project_id=project_id,
                author={"id": authenticated_human_actor_id, "kind": "human"}, kind="human_message",
                body=clean_body, created_at=stamp, root_message_id=root_id,
            )
            state["messages"][created.id] = created.to_dict()
            public_snapshot = self._view_snapshot_locked(
                state, tenant_id=tenant_id, project_id=project_id,
                message=created, viewer_id=authenticated_human_actor_id,
            )
            self._record_generic_idempotency(
                state, identity, body_hash, operation=operation,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id, created_at=stamp,
                message_id=created.id, response_snapshot=created.to_dict(),
                extra_ref={
                    "parent_message_id": parent_message_id,
                    "public_snapshot": public_snapshot,
                },
            )
            self._append_wake_event(
                state, project_id=project_id, event_type="conversation.changed", occurred_at=stamp,
            )
            return created

        return self.repository.mutate_conversation_state(tenant_id, project_id, mutate)

    def reply_response_view(
        self, *, tenant_id: str, project_id: str, authenticated_human_actor_id: str,
        parent_message_id: str, client_request_id: str, body: str,
    ) -> ConversationMessageView:
        """Return the exact public projection committed for a reply identity."""
        self._require_current_member(tenant_id, project_id, authenticated_human_actor_id)
        clean_body = _screen_text(body.strip())
        identity = self._identity(
            tenant_id, project_id, authenticated_human_actor_id,
            "conversation_reply", client_request_id,
        )
        body_hash = canonical_body_hash({
            "method": "POST", "parent_message_id": parent_message_id,
            "body": {"client_request_id": client_request_id, "body": clean_body},
        })
        state = self.repository.conversation_state(tenant_id, project_id)
        snapshot = self._generic_replay(
            state, identity, body_hash, operation="conversation_reply",
            authenticated_human_actor_id=authenticated_human_actor_id,
            client_request_id=client_request_id,
        )
        if snapshot is None:
            raise ConversationConflictError("idempotency_corrupt")
        try:
            message = ConversationMessage.from_dict(snapshot)
        except Exception as exc:
            raise ConversationConflictError("idempotency_corrupt") from exc
        if (
            message.to_dict() != dict(snapshot)
            or message.tenant_id != tenant_id
            or message.project_id != project_id
            or message.author.get("id") != authenticated_human_actor_id
            or message.author.get("kind") != "human"
        ):
            raise ConversationConflictError("idempotency_corrupt")
        record = state["idempotency"][identity]
        response_ref = record["response_ref"]
        raw_view = response_ref.get("public_snapshot")
        if raw_view is None:
            # Expand-only compatibility for reply records written before the
            # exact public snapshot became part of the command response.
            return self.message_view(
                tenant_id, project_id, message.id, authenticated_human_actor_id,
            )
        thread, reactions, saved = self._validated_view_snapshot(
            raw_view, tenant_id=tenant_id, project_id=project_id,
        )
        return ConversationMessageView(message, thread, reactions, saved)

    def _change_reaction(
        self, *, method: str, tenant_id: str, project_id: str, authenticated_human_actor_id: str,
        message_id: str, reaction: str, client_request_id: str,
    ) -> ReactionMutationResult:
        self._require_human_member(tenant_id, project_id, authenticated_human_actor_id)
        validate_scope_id(message_id, "message_id")
        if reaction not in CONVERSATION_REACTIONS:
            raise ValidationError("invalid conversation reaction")
        operation = "conversation_reaction_put" if method == "PUT" else "conversation_reaction_delete"
        identity = self._identity(
            tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id,
        )
        body_hash = canonical_body_hash({
            "method": method, "message_id": message_id, "reaction": reaction,
            "body": {"client_request_id": client_request_id},
        })

        def mutate(state: dict[str, Any]) -> ReactionMutationResult:
            self._require_human_member_locked(tenant_id, project_id, authenticated_human_actor_id)
            snapshot = self._generic_replay(
                state, identity, body_hash, operation=operation,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id, expected_message_id=message_id,
            )
            if snapshot is not None:
                raw_message, raw_reactions = snapshot.get("message"), snapshot.get("reactions")
                if (
                    set(snapshot) not in ({"message", "reactions"}, {"message", "reactions", "view"})
                    or not isinstance(raw_message, Mapping)
                    or not isinstance(raw_reactions, list)
                ):
                    raise ConversationConflictError("idempotency_corrupt")
                try:
                    message = ConversationMessage.from_dict(raw_message)
                except Exception as exc:
                    raise ConversationConflictError("idempotency_corrupt") from exc
                expected_keys = {"reaction", "count", "reacted"}
                if (
                    dict(raw_message) != message.to_dict()
                    or message.id != message_id
                    or message.tenant_id != tenant_id
                    or message.project_id != project_id
                    or any(
                    not isinstance(item, dict) or set(item) != expected_keys for item in raw_reactions
                    )
                ):
                    raise ConversationConflictError("idempotency_corrupt")
                raw_view = snapshot.get("view")
                if raw_view is None:
                    view = self._view_snapshot_locked(
                        state, tenant_id=tenant_id, project_id=project_id,
                        message=message, viewer_id=authenticated_human_actor_id,
                    )
                    thread = {
                        "reply_count": view["thread"]["reply_count"],
                        "latest_replies": [
                            ConversationMessage.from_dict(item)
                            for item in view["thread"]["latest_replies"]
                        ],
                    }
                    saved = bool(view["saved"])
                    try:
                        reactions = tuple(_validated_reactions(raw_reactions))
                    except ValidationError as exc:
                        raise ConversationConflictError("idempotency_corrupt") from exc
                else:
                    thread, reactions, saved = self._validated_view_snapshot(
                        raw_view, tenant_id=tenant_id, project_id=project_id,
                    )
                    if list(reactions) != raw_reactions:
                        raise ConversationConflictError("idempotency_corrupt")
                return ReactionMutationResult(message, reactions, thread, saved)
            message = self._resolve_message_locked(
                state, tenant_id=tenant_id, project_id=project_id, message_id=message_id,
            )
            key = self._reaction_key(message_id, authenticated_human_actor_id, reaction)
            stamp = self._clock()
            if method == "PUT":
                state["reactions"].setdefault(key, ConversationReaction(
                    message_id=message_id, actor_id=authenticated_human_actor_id,
                    reaction=reaction, created_at=stamp,
                ).to_dict())
            else:
                state["reactions"].pop(key, None)
            view = self._view_snapshot_locked(
                state, tenant_id=tenant_id, project_id=project_id,
                message=message, viewer_id=authenticated_human_actor_id,
            )
            thread, projected, saved = self._validated_view_snapshot(
                view, tenant_id=tenant_id, project_id=project_id,
            )
            result = ReactionMutationResult(message, projected, thread, saved)
            self._record_generic_idempotency(
                state, identity, body_hash, operation=operation,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id, created_at=stamp, message_id=message_id,
                response_snapshot={
                    "message": message.to_dict(),
                    "reactions": list(projected),
                    "view": view,
                },
            )
            self._append_wake_event(
                state, project_id=project_id, event_type="conversation.changed", occurred_at=stamp,
            )
            return result

        return self.repository.mutate_conversation_state(tenant_id, project_id, mutate)

    def put_reaction(self, **kwargs: Any) -> ReactionMutationResult:
        return self._change_reaction(method="PUT", **kwargs)

    def delete_reaction(self, **kwargs: Any) -> ReactionMutationResult:
        return self._change_reaction(method="DELETE", **kwargs)

    def _change_saved(
        self, *, saved: bool, tenant_id: str, project_id: str, authenticated_human_actor_id: str,
        message_id: str, client_request_id: str,
    ) -> SavedMutationResult:
        self._require_human_member(tenant_id, project_id, authenticated_human_actor_id)
        validate_scope_id(message_id, "message_id")
        method = "PUT" if saved else "DELETE"
        operation = "conversation_saved_put" if saved else "conversation_saved_delete"
        identity = self._identity(
            tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id,
        )
        body_hash = canonical_body_hash({
            "method": method, "message_id": message_id,
            "body": {"client_request_id": client_request_id},
        })

        def mutate(state: dict[str, Any]) -> SavedMutationResult:
            self._require_human_member_locked(tenant_id, project_id, authenticated_human_actor_id)
            snapshot = self._generic_replay(
                state, identity, body_hash, operation=operation,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id, expected_message_id=message_id,
            )
            if snapshot is not None:
                if set(snapshot) != {"saved"} or not isinstance(snapshot.get("saved"), bool):
                    raise ConversationConflictError("idempotency_corrupt")
                return SavedMutationResult(bool(snapshot["saved"]))
            self._resolve_message_locked(
                state, tenant_id=tenant_id, project_id=project_id, message_id=message_id,
            )
            key = self._saved_key(authenticated_human_actor_id, message_id)
            stamp = self._clock()
            if saved:
                state["saved_references"].setdefault(key, SavedReference(
                    tenant_id=tenant_id, human_id=authenticated_human_actor_id,
                    object_kind="conversation_message", object_id=message_id, created_at=stamp,
                ).to_dict())
            else:
                state["saved_references"].pop(key, None)
            result = SavedMutationResult(saved)
            self._record_generic_idempotency(
                state, identity, body_hash, operation=operation,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id, created_at=stamp, message_id=message_id,
                response_snapshot={"saved": saved},
            )
            self._append_wake_event(
                state, project_id=project_id, event_type="saved.changed", occurred_at=stamp,
                recipient_human_id=authenticated_human_actor_id,
            )
            return result

        return self.repository.mutate_conversation_state(tenant_id, project_id, mutate)

    def put_saved(self, **kwargs: Any) -> SavedMutationResult:
        return self._change_saved(saved=True, **kwargs)

    def delete_saved(self, **kwargs: Any) -> SavedMutationResult:
        return self._change_saved(saved=False, **kwargs)

    def _change_message(self, *, operation: str, tenant_id: str, project_id: str, authenticated_human_actor_id: str,
                        message_id: str, client_request_id: str, expected_revision: int, body: str | None = None) -> ConversationMessage:
        self._require_human_member(tenant_id, project_id, authenticated_human_actor_id)
        validate_scope_id(message_id, "message_id")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 1:
            raise ValidationError("expected_revision is required")
        if operation == "edit":
            if not isinstance(body, str) or not body.strip():
                raise ValidationError("message body is required")
            body = _screen_text(body.strip())
        elif body is not None:
            raise ValidationError("delete messages do not accept a body")
        identity = self._identity(tenant_id, project_id, authenticated_human_actor_id, operation, client_request_id)
        body_hash = canonical_body_hash({"message_id": message_id, "expected_revision": expected_revision, "body": body})

        def mutate(state: dict[str, Any]) -> ConversationMessage:
            self._require_human_member_locked(tenant_id, project_id, authenticated_human_actor_id)
            replay = self._replay(
                state, identity, body_hash, operation=operation,
                tenant_id=tenant_id, project_id=project_id,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id,
            )
            if replay is not None:
                return replay
            row = state["messages"].get(message_id)
            if not isinstance(row, dict):
                raise NotFoundError("conversation message not found")
            current = ConversationMessage.from_dict(row)
            if current.revision != expected_revision:
                raise ConversationConflictError("revision_conflict")
            if current.deleted_at is not None:
                raise ConversationConflictError("message_deleted")
            if current.author.get("id") != authenticated_human_actor_id:
                raise AuthorizationError("only the message author may change it")
            stamp = self._clock()
            if operation == "edit":
                changed = replace(current, body=body, revision=current.revision + 1, edited_at=stamp)
            else:
                # Links are intentionally retained as durable evidence/source
                # provenance; the tombstone makes the body unavailable.
                changed = replace(current, body=None, revision=current.revision + 1, deleted_at=stamp, edited_at=stamp)
            audit = MessageAudit(
                id=self._id_factory("msg_audit"), message_id=message_id, operation=operation,
                actor_id=authenticated_human_actor_id, client_request_id=client_request_id,
                prior_revision=current.revision, prior_body=current.body,
                resulting_revision=changed.revision, occurred_at=stamp,
            )
            state["messages"][message_id] = changed.to_dict()
            state["message_audits"][audit.id] = audit.to_dict()
            self._record_idempotency(
                state, identity, body_hash, changed, operation=operation,
                authenticated_human_actor_id=authenticated_human_actor_id,
                client_request_id=client_request_id, created_at=stamp,
            )
            self._append_wake_event(
                state, project_id=project_id, event_type="conversation.changed", occurred_at=stamp,
            )
            return changed

        return self.repository.mutate_conversation_state(tenant_id, project_id, mutate)

    def edit_message(self, **kwargs: Any) -> ConversationMessage:
        return self._change_message(operation="edit", **kwargs)

    def delete_message(self, **kwargs: Any) -> ConversationMessage:
        return self._change_message(operation="delete", **kwargs)

    def messages(self, tenant_id: str, project_id: str) -> list[ConversationMessage]:
        state = self.repository.conversation_state(tenant_id, project_id)
        rows = [ConversationMessage.from_dict(row) for row in state["messages"].values()]
        # V0 comments are a read-only legacy projection.  They are never copied
        # into conversation_state, so upgrades cannot duplicate or erase them.
        for comment in self.repository.list_comments(tenant_id, project_id):
            if comment.id in state["messages"]:
                continue
            rows.append(ConversationMessage(
                id=comment.id, tenant_id=comment.tenant_id, project_id=comment.project_id,
                author={"id": comment.author_id, "kind": "human"}, kind="human_message",
                body=_screen_text(comment.body), created_at=comment.created_at, revision=comment.revision,
                links={"work_item_id": comment.task_id, "run_id": None, "output_id": None},
            ))
        return sorted(rows, key=lambda item: (item.created_at, item.id))

    def roots(self, tenant_id: str, project_id: str) -> list[ConversationMessage]:
        return [item for item in self.messages(tenant_id, project_id) if item.root_message_id is None]

    def replies(self, tenant_id: str, project_id: str, parent_message_id: str) -> list[ConversationMessage]:
        validate_scope_id(parent_message_id, "parent_message_id")
        rows = self.messages(tenant_id, project_id)
        parent = next((item for item in rows if item.id == parent_message_id), None)
        if parent is None:
            raise NotFoundError("conversation message not found")
        root_id = parent.root_message_id or parent.id
        return [item for item in rows if item.root_message_id == root_id]

    def message_view(
        self, tenant_id: str, project_id: str, message_id: str, authenticated_human_actor_id: str,
    ) -> ConversationMessageView:
        self._require_current_member(tenant_id, project_id, authenticated_human_actor_id)
        validate_scope_id(message_id, "message_id")
        all_messages = self.messages(tenant_id, project_id)
        message = next((item for item in all_messages if item.id == message_id), None)
        if message is None:
            raise NotFoundError("conversation message not found")
        state = self.repository.conversation_state(tenant_id, project_id)
        reply_rows = [item for item in all_messages if item.root_message_id == message.id] if message.root_message_id is None else []
        reactions = self._reaction_projection(state, message.id, authenticated_human_actor_id)
        saved_key = self._saved_key(authenticated_human_actor_id, message.id)
        raw_saved = state["saved_references"].get(saved_key)
        if raw_saved is not None:
            if not isinstance(raw_saved, Mapping):
                raise ConversationConflictError("conversation_state_corrupt")
            try:
                saved_reference = SavedReference.from_dict(raw_saved)
            except Exception as exc:
                raise ConversationConflictError("conversation_state_corrupt") from exc
            if dict(raw_saved) != saved_reference.to_dict():
                raise ConversationConflictError("conversation_state_corrupt")
        return ConversationMessageView(
            message=message,
            thread={"reply_count": len(reply_rows), "latest_replies": reply_rows[-3:]},
            reactions=reactions,
            saved=raw_saved is not None,
        )

    def audits(self, tenant_id: str, project_id: str) -> list[MessageAudit]:
        state = self.repository.conversation_state(tenant_id, project_id)
        return sorted((MessageAudit.from_dict(row) for row in state["message_audits"].values()), key=lambda item: (item.occurred_at, item.id))
