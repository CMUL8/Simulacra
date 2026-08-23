"""Activity Inbox queries, durable read positions, and away summaries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable

from .models import DomainEvent, iso_now
from .repository import JsonCollaborationRepository
from .errors import AuthorizationError


class InboxCategory(StrEnum):
	ACTIVITY = "activity"
	ASSIGNED = "assigned"
	MENTIONS = "mentions"
	REVIEWS = "reviews"
	APPROVALS = "approvals"
	FAILURES = "failures"
	DEPLOYMENTS = "deployments"


@dataclass(frozen=True, slots=True)
class ActivityItem:
	position: int
	event: DomainEvent
	category: InboxCategory
	unread: bool
	target_type: str | None
	target_id: str | None
	graph_path: str | None
	graph_revision: str | None

	@property
	def deep_link(self) -> dict[str, str | None]:
		return {
			"project_id": self.event.project_id,
			"task_id": self.event.task_id,
			"target_type": self.target_type,
			"target_id": self.target_id,
			"graph_path": self.graph_path,
			"graph_revision": self.graph_revision,
		}


@dataclass(frozen=True, slots=True)
class AwaySummary:
	since: str | None
	total: int
	unread: int
	counts: dict[str, int]
	highlights: tuple[ActivityItem, ...]


class ActivityInbox:
	def __init__(self, repository: JsonCollaborationRepository):
		self.repository = repository

	def _assert_member(self, tenant_id: str, project_id: str, actor_id: str) -> None:
		room = self.repository.get_room(tenant_id, project_id)
		if actor_id not in {member.actor_id for member in room.members}:
			raise AuthorizationError("actor is not a project room member")

	@staticmethod
	def _category(event: DomainEvent) -> InboxCategory:
		explicit = str(event.payload.get("category") or "").lower()
		if explicit in InboxCategory._value2member_map_:
			return InboxCategory(explicit)
		action = event.action.lower()
		if "mention" in action:
			return InboxCategory.MENTIONS
		if "review" in action:
			return InboxCategory.REVIEWS
		if "approval" in action or "approved" in action:
			return InboxCategory.APPROVALS
		if "deploy" in action:
			return InboxCategory.DEPLOYMENTS
		if event.result.lower() in {"failed", "failure", "error", "rejected"}:
			return InboxCategory.FAILURES
		if "assign" in action or "claim" in action:
			return InboxCategory.ASSIGNED
		return InboxCategory.ACTIVITY

	@staticmethod
	def _for_actor(event: DomainEvent, actor_id: str, category: InboxCategory) -> bool:
		payload = event.payload
		if category == InboxCategory.MENTIONS:
			return actor_id in payload.get("mention_ids", [])
		if category == InboxCategory.ASSIGNED:
			return payload.get("assignee_id") == actor_id
		if category == InboxCategory.REVIEWS:
			return payload.get("reviewer_id") in {None, actor_id} or payload.get("owner_id") == actor_id
		if category == InboxCategory.APPROVALS:
			return payload.get("approver_id") in {None, actor_id}
		return True

	def query(
		self, *, tenant_id: str, project_id: str, actor_id: str,
		categories: Iterable[InboxCategory | str] | None = None, unread_only: bool = False,
		task_id: str | None = None, actor_filter: str | None = None,
		result: str | None = None, limit: int | None = None,
	) -> list[ActivityItem]:
		self._assert_member(tenant_id, project_id, actor_id)
		selected = {InboxCategory(value) for value in categories} if categories else None
		state = self.repository.get_inbox_state(tenant_id, project_id, actor_id)
		read_position = int(state.get("last_read_position", 0))
		items: list[ActivityItem] = []
		for position, event in enumerate(self.repository.list_events(tenant_id, project_id), 1):
			category = self._category(event)
			unread = position > read_position
			if selected is not None and category not in selected:
				continue
			if unread_only and not unread:
				continue
			if task_id is not None and event.task_id != task_id:
				continue
			if actor_filter is not None and event.actor_id != actor_filter:
				continue
			if result is not None and event.result != result:
				continue
			if not self._for_actor(event, actor_id, category):
				continue
			payload = event.payload
			items.append(ActivityItem(
				position=position, event=event, category=category, unread=unread,
				target_type=payload.get("target_type"), target_id=payload.get("target_id"),
				graph_path=payload.get("graph_path"), graph_revision=payload.get("graph_revision"),
			))
		items.reverse()
		return items[:limit] if limit is not None else items

	def mark_read(
		self, *, tenant_id: str, project_id: str, actor_id: str,
		position: int | None = None, event_id: str | None = None,
	) -> dict[str, Any]:
		self._assert_member(tenant_id, project_id, actor_id)
		events = self.repository.list_events(tenant_id, project_id)
		if event_id is not None:
			matches = [index for index, event in enumerate(events, 1) if event.id == event_id]
			if not matches:
				from .errors import NotFoundError
				raise NotFoundError("inbox event not found")
			position = matches[0]
		if position is None:
			position = len(events)
		if position > len(events):
			from .errors import ConflictError
			raise ConflictError("read position is beyond the event log")
		return self.repository.save_inbox_state(
			tenant_id, project_id, actor_id, last_read_position=position, updated_at=iso_now()
		)

	def while_you_were_away(
		self, *, tenant_id: str, project_id: str, actor_id: str,
		since: str | None = None, highlight_limit: int = 5,
	) -> AwaySummary:
		items = self.query(tenant_id=tenant_id, project_id=project_id, actor_id=actor_id)
		if since is not None:
			boundary = datetime.fromisoformat(since)
			if boundary.tzinfo is None:
				raise ValueError("since must be timezone-aware")
			items = [item for item in items if datetime.fromisoformat(item.event.timestamp) > boundary]
		else:
			items = [item for item in items if item.unread]
		counts = {category.value: 0 for category in InboxCategory}
		for item in items:
			counts[item.category.value] += 1
		priority = {InboxCategory.FAILURES: 0, InboxCategory.APPROVALS: 1, InboxCategory.REVIEWS: 2,
			InboxCategory.MENTIONS: 3, InboxCategory.ASSIGNED: 4}
		highlights = sorted(items, key=lambda item: (priority.get(item.category, 9), -item.position))[:highlight_limit]
		return AwaySummary(since=since, total=len(items), unread=sum(item.unread for item in items),
			counts=counts, highlights=tuple(highlights))
