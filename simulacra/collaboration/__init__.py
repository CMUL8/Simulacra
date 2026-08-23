"""Public CMUL8 V0 collaboration domain API."""

from .errors import (
	AuthorizationError,
	CollaborationError,
	ConflictError,
	InvalidTransitionError,
	NotFoundError,
	ScopeError,
	ValidationError,
)
from .events import LegacyEventProjector, make_domain_event, project_legacy_event
from .inbox import ActivityInbox, ActivityItem, AwaySummary, InboxCategory
from .models import (
	ActorType,
	Comment,
	CommentTargetType,
	DomainEvent,
	Member,
	Mention,
	ProjectRoom,
	Review,
	ReviewDecision,
	Task,
	TaskState,
	normalize_mentions,
)
from .presence import Presence, PresenceRegistry
from .repository import CollaborationRepository, JsonCollaborationRepository
from .service import CollaborationService

JSONCollaborationRepository = JsonCollaborationRepository
FileCollaborationRepository = JsonCollaborationRepository

__all__ = [
	"ActivityInbox", "ActivityItem", "ActorType", "AuthorizationError", "AwaySummary",
	"CollaborationError", "CollaborationRepository", "CollaborationService", "Comment",
	"CommentTargetType", "ConflictError", "DomainEvent", "InboxCategory",
	"InvalidTransitionError", "JsonCollaborationRepository", "LegacyEventProjector", "Member",
	"Mention", "NotFoundError", "Presence", "PresenceRegistry", "ProjectRoom", "Review",
	"ReviewDecision", "ScopeError", "Task", "TaskState", "ValidationError", "make_domain_event",
	"normalize_mentions", "project_legacy_event",
	"JSONCollaborationRepository", "FileCollaborationRepository",
]
