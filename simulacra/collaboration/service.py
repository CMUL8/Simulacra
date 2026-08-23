"""Secure collaboration command service with optimistic concurrency."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from .errors import AuthorizationError, ConflictError, InvalidTransitionError, ValidationError
from .events import make_domain_event
from .models import (
	ActorType,
	Comment,
	CommentTargetType,
	Member,
	ProjectRoom,
	Review,
	ReviewDecision,
	Task,
	TaskState,
	iso_now,
	new_id,
	normalize_mentions,
	validate_scope_id,
)
from .repository import CollaborationRepository

_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
	TaskState.PROPOSED: frozenset({TaskState.READY, TaskState.CANCELLED}),
	TaskState.READY: frozenset({TaskState.WORKING, TaskState.BLOCKED, TaskState.CANCELLED}),
	TaskState.WORKING: frozenset({TaskState.IN_REVIEW, TaskState.BLOCKED, TaskState.FAILED, TaskState.CANCELLED}),
	TaskState.IN_REVIEW: frozenset({TaskState.WORKING, TaskState.DONE, TaskState.FAILED, TaskState.CANCELLED}),
	TaskState.BLOCKED: frozenset({TaskState.READY, TaskState.WORKING, TaskState.FAILED, TaskState.CANCELLED}),
	TaskState.FAILED: frozenset({TaskState.READY, TaskState.CANCELLED}),
	TaskState.DONE: frozenset(),
	TaskState.CANCELLED: frozenset(),
}


class CollaborationService:
	def __init__(self, repository: CollaborationRepository):
		self.repository = repository

	def _room_member(self, tenant_id: str, project_id: str, actor_id: str) -> Member:
		room = self.repository.get_room(tenant_id, project_id)
		for member in room.members:
			if member.actor_id == actor_id:
				return member
		raise AuthorizationError("actor is not a project room member")

	def _emit(
		self,
		*,
		tenant_id: str,
		project_id: str,
		actor_id: str,
		action: str,
		result: str = "succeeded",
		task: Task | None = None,
		payload: dict[str, Any] | None = None,
		actor_type: ActorType = ActorType.HUMAN,
	) -> None:
		self.repository.append_event(make_domain_event(
			tenant_id=tenant_id,
			project_id=project_id,
			actor_type=actor_type,
			actor_id=actor_id,
			action=action,
			result=result,
			task_id=task.id if task else None,
			operation_graph_version=task.operation_graph_version if task else None,
			application_version=task.application_version if task else None,
			payload=payload,
		))

	def create_room(
		self, *, tenant_id: str, project_id: str, creator_id: str, creator_role: str = "owner"
	) -> ProjectRoom:
		validate_scope_id(creator_id, "creator_id")
		room = ProjectRoom(
			id=new_id("room"), tenant_id=tenant_id, project_id=project_id,
			members=[Member(actor_id=creator_id, role=creator_role)],
		)
		created = self.repository.create_room(room)
		self._emit(tenant_id=tenant_id, project_id=project_id, actor_id=creator_id,
			action="room.created", payload={"room_id": room.id, "category": "activity"})
		return created

	def add_member(
		self, *, tenant_id: str, project_id: str, actor_id: str, member_id: str,
		role: str, expected_revision: int
	) -> ProjectRoom:
		actor = self._room_member(tenant_id, project_id, actor_id)
		if actor.role not in {"owner", "admin"}:
			raise AuthorizationError("only owners and admins can add members")
		validate_scope_id(member_id, "member_id")
		room = self.repository.get_room(tenant_id, project_id)
		if any(member.actor_id == member_id for member in room.members):
			raise ConflictError("member already belongs to project room")
		updated = replace(room, members=[*room.members, Member(actor_id=member_id, role=role)],
			revision=room.revision + 1, updated_at=iso_now())
		updated = self.repository.save_room(updated, expected_revision)
		self._emit(tenant_id=tenant_id, project_id=project_id, actor_id=actor_id,
			action="room.member_added", payload={"category": "activity", "member_id": member_id, "role": role})
		return updated

	def create_task(
		self, *, tenant_id: str, project_id: str, actor_id: str, title: str, objective: str,
		acceptance_criteria: list[str], source_message_id: str | None = None,
		owner_id: str | None = None, collaborator_ids: list[str] | None = None,
		operation_graph_version: str | None = None, application_version: str | None = None,
	) -> Task:
		self._room_member(tenant_id, project_id, actor_id)
		if not title.strip() or not objective.strip() or not acceptance_criteria:
			raise ValidationError("title, objective, and acceptance criteria are required")
		if owner_id is not None:
			self._room_member(tenant_id, project_id, owner_id)
		collaborators = sorted(set(collaborator_ids or []))
		for collaborator in collaborators:
			self._room_member(tenant_id, project_id, collaborator)
		if owner_id in collaborators:
			collaborators.remove(owner_id)
		task = Task(
			id=new_id("task"), tenant_id=tenant_id, project_id=project_id, title=title.strip(),
			objective=objective.strip(), acceptance_criteria=[item.strip() for item in acceptance_criteria if item.strip()],
			source_message_id=source_message_id, owner_id=owner_id, collaborator_ids=collaborators,
			state=TaskState.READY if owner_id else TaskState.PROPOSED,
			operation_graph_version=operation_graph_version, application_version=application_version,
		)
		created = self.repository.create_task(task)
		self._emit(tenant_id=tenant_id, project_id=project_id, actor_id=actor_id, action="task.created",
			task=task, payload={"category": "assigned" if owner_id else "activity", "assignee_id": owner_id,
			"target_type": "task", "target_id": task.id})
		return created

	def set_collaborators(
		self, *, tenant_id: str, project_id: str, task_id: str, actor_id: str,
		collaborator_ids: list[str], expected_revision: int,
	) -> Task:
		self._room_member(tenant_id, project_id, actor_id)
		task = self.repository.get_task(tenant_id, project_id, task_id)
		if task.revision != expected_revision:
			raise ConflictError(f"stale task revision: expected {expected_revision}, current {task.revision}")
		if actor_id != task.owner_id:
			raise AuthorizationError("only the accountable owner can change collaborators")
		collaborators = sorted(set(collaborator_ids))
		if task.owner_id in collaborators:
			collaborators.remove(task.owner_id)
		for collaborator in collaborators:
			self._room_member(tenant_id, project_id, collaborator)
		updated = replace(task, collaborator_ids=collaborators, revision=task.revision + 1, updated_at=iso_now())
		updated = self.repository.save_task(updated, expected_revision)
		self._emit(tenant_id=tenant_id, project_id=project_id, actor_id=actor_id,
			action="task.collaborators_changed", task=updated,
			payload={"category": "assigned", "collaborator_ids": collaborators,
				"target_type": "task", "target_id": task.id})
		return updated

	def claim_task(
		self, *, tenant_id: str, project_id: str, task_id: str, actor_id: str, expected_revision: int
	) -> Task:
		self._room_member(tenant_id, project_id, actor_id)
		task = self.repository.get_task(tenant_id, project_id, task_id)
		if task.revision != expected_revision:
			raise ConflictError(f"stale task revision: expected {expected_revision}, current {task.revision}")
		if task.owner_id is not None:
			raise ConflictError("task already has an accountable owner")
		if task.state != TaskState.PROPOSED:
			raise ConflictError("only a proposed task can be claimed")
		updated = replace(task, owner_id=actor_id, state=TaskState.READY, revision=task.revision + 1,
			updated_at=iso_now(), activity=[*task.activity, {"action": "claimed", "actor_id": actor_id, "at": iso_now()}])
		updated = self.repository.save_task(updated, expected_revision)
		self._emit(tenant_id=tenant_id, project_id=project_id, actor_id=actor_id, action="task.claimed", task=updated,
			payload={"category": "assigned", "assignee_id": actor_id, "target_type": "task", "target_id": task.id})
		return updated

	def transition_task(
		self, *, tenant_id: str, project_id: str, task_id: str, actor_id: str,
		to_state: TaskState | str, expected_revision: int, result: dict[str, Any] | None = None,
		activity_detail: str = ""
	) -> Task:
		self._room_member(tenant_id, project_id, actor_id)
		task = self.repository.get_task(tenant_id, project_id, task_id)
		if task.revision != expected_revision:
			raise ConflictError(f"stale task revision: expected {expected_revision}, current {task.revision}")
		state = TaskState(to_state)
		if state not in _TRANSITIONS[task.state]:
			raise InvalidTransitionError(f"invalid task transition: {task.state.value} -> {state.value}")
		if task.owner_id is None:
			raise ConflictError("task must be atomically claimed before state changes")
		if actor_id != task.owner_id and actor_id not in task.collaborator_ids:
			raise AuthorizationError("only task participants can change task state")
		activity = {"action": "state_changed", "from": task.state.value, "to": state.value,
			"actor_id": actor_id, "detail": activity_detail, "at": iso_now()}
		updated = replace(task, state=state, result=result if result is not None else task.result,
			activity=[*task.activity, activity], revision=task.revision + 1, updated_at=iso_now())
		updated = self.repository.save_task(updated, expected_revision)
		category = "failures" if state == TaskState.FAILED else "activity"
		self._emit(tenant_id=tenant_id, project_id=project_id, actor_id=actor_id,
			action="task.state_changed", result="failed" if state == TaskState.FAILED else "succeeded", task=updated,
			payload={"category": category, "from": task.state.value, "to": state.value,
			"target_type": "task", "target_id": task.id})
		return updated

	def add_comment(
		self, *, tenant_id: str, project_id: str, author_id: str, body: str,
		target_type: CommentTargetType | str, target_id: str | None = None, task_id: str | None = None,
		graph_path: str | None = None, graph_revision: str | None = None,
		mentions: list[Any] | None = None,
	) -> Comment:
		self._room_member(tenant_id, project_id, author_id)
		if not body.strip():
			raise ValidationError("comment body is required")
		target = CommentTargetType(target_type)
		if target == CommentTargetType.PROJECT:
			target_id = target_id or project_id
			if target_id != project_id or task_id or graph_path or graph_revision:
				raise ValidationError("project comments must target the scoped project")
		elif target == CommentTargetType.TASK:
			task_id = task_id or target_id
			if not task_id:
				raise ValidationError("task comment requires task_id")
			self.repository.get_task(tenant_id, project_id, task_id)
			target_id = task_id
			if graph_path or graph_revision:
				raise ValidationError("task comments cannot include graph coordinates")
		else:
			if not graph_path or not graph_revision:
				raise ValidationError("graph comments require an exact path and revision")
			if ".." in graph_path.split("/") or "\\" in graph_path:
				raise ValidationError("invalid graph element path")
			target_id = target_id or graph_path
		if not target_id:
			raise ValidationError("comment target_id is required")
		comment = Comment(
			id=new_id("comment"), tenant_id=tenant_id, project_id=project_id, author_id=author_id,
			body=body.strip(), target_type=target, target_id=target_id, task_id=task_id,
			graph_path=graph_path, graph_revision=graph_revision,
			mentions=normalize_mentions(mentions or []),
		)
		created = self.repository.create_comment(comment)
		self._emit(tenant_id=tenant_id, project_id=project_id, actor_id=author_id, action="comment.created",
			payload={"category": "mentions" if comment.mentions else "activity", "body": body.strip(),
			"mention_ids": [item.ref_id for item in comment.mentions if item.ref_type == "actor"],
			"target_type": target.value, "target_id": target_id, "graph_path": graph_path,
			"graph_revision": graph_revision}, task=self.repository.get_task(tenant_id, project_id, task_id) if task_id else None)
		return created

	def review_task(
		self, *, tenant_id: str, project_id: str, task_id: str, reviewer_id: str,
		decision: ReviewDecision | str, expected_revision: int, reviewer_role: str | None = None,
		actor_type: ActorType | str = ActorType.HUMAN, body: str = "", allow_self_review: bool = False,
	) -> tuple[Review, Task]:
		member = self._room_member(tenant_id, project_id, reviewer_id)
		task = self.repository.get_task(tenant_id, project_id, task_id)
		if task.revision != expected_revision:
			raise ConflictError(f"stale task revision: expected {expected_revision}, current {task.revision}")
		choice = ReviewDecision(decision)
		if task.owner_id == reviewer_id and not allow_self_review:
			raise AuthorizationError("task owner cannot review their own work")
		if ActorType(actor_type) == ActorType.HUMAN and member.role not in {"owner", "admin", "approver", "reviewer"}:
			raise AuthorizationError("task review requires an owner, admin, approver, or reviewer role")
		if choice == ReviewDecision.ROLLBACK:
			if task.state != TaskState.DONE:
				raise InvalidTransitionError("rollback requires a done task")
			new_state = TaskState.WORKING
		elif task.state != TaskState.IN_REVIEW:
			raise InvalidTransitionError("review decisions require a task in review")
		elif choice == ReviewDecision.APPROVE:
			new_state = TaskState.DONE
		elif choice == ReviewDecision.REJECT:
			new_state = TaskState.FAILED
		elif choice == ReviewDecision.REQUEST_CHANGES:
			new_state = TaskState.WORKING
		else:
			new_state = TaskState.IN_REVIEW
		review = Review(
			id=new_id("review"), tenant_id=tenant_id, project_id=project_id, task_id=task_id,
			reviewer_id=reviewer_id, reviewer_role=reviewer_role or member.role,
			actor_type=ActorType(actor_type), decision=choice, body=body.strip(), task_revision=task.revision,
		)
		updated = replace(task, state=new_state, revision=task.revision + 1, updated_at=iso_now(),
			activity=[*task.activity, {"action": "reviewed", "decision": choice.value,
			"actor_id": reviewer_id, "role": review.reviewer_role, "at": iso_now()}])
		updated = self.repository.save_task(updated, expected_revision)
		self.repository.create_review(review)
		self._emit(tenant_id=tenant_id, project_id=project_id, actor_id=reviewer_id,
			action="task.reviewed", result="rejected" if choice == ReviewDecision.REJECT else "succeeded",
			task=updated, actor_type=ActorType(actor_type), payload={"category": "reviews", "decision": choice.value,
			"owner_id": task.owner_id, "target_type": "task", "target_id": task.id})
		return review, updated
