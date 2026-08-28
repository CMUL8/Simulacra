"""Durable Project Room authority required for product mutations."""

from __future__ import annotations

from contextlib import contextmanager

from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.errors import AuthorizationError, CollaborationError

from .paths import RUNS_DIR

_collaboration_root = RUNS_DIR / ".cmul8-control"


def require_room_mutation_authority(
	project_id: str, *, tenant_id: str, actor_id: str | None,
) -> None:
	"""Require a current durable Project Room owner/admin for source changes."""
	if not actor_id:
		raise PermissionError("a Project Room owner or admin is required for project mutation")
	try:
		repository = JsonCollaborationRepository(_collaboration_root)
		room = repository.get_room(tenant_id, project_id)
	except CollaborationError as exc:
		raise PermissionError("a Project Room owner or admin is required for project mutation") from exc
	member = repository.visible_member(room, actor_id)
	if member is None or member.role not in {"owner", "admin"}:
		raise PermissionError("a Project Room owner or admin is required for project mutation")


def has_room_mutation_authority(
	project_id: str, *, tenant_id: str, actor_id: str | None,
) -> bool:
	try:
		require_room_mutation_authority(project_id, tenant_id=tenant_id, actor_id=actor_id)
	except PermissionError:
		return False
	return True


@contextmanager
def room_mutation_commit(
	project_id: str, *, tenant_id: str, actor_id: str,
):
	"""Lock the room and authorize the final immutable mutation atomically."""
	service = CollaborationService(JsonCollaborationRepository(_collaboration_root))
	try:
		with service.mutation_authority_lock(
			tenant_id=tenant_id, project_id=project_id, actor_id=actor_id,
		):
			yield
	except AuthorizationError as exc:
		raise PermissionError("a Project Room owner or admin is required for project mutation") from exc
