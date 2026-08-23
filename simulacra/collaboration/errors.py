"""Collaboration domain errors independent of any HTTP transport."""


class CollaborationError(Exception):
	"""Base collaboration error."""


class ValidationError(CollaborationError, ValueError):
	"""A command or durable record is invalid."""


class NotFoundError(CollaborationError, LookupError):
	"""A scoped collaboration record does not exist."""


class ConflictError(CollaborationError):
	"""An optimistic write, claim, or idempotency check conflicted."""


class ScopeError(CollaborationError, PermissionError):
	"""A record or path escaped the requested tenant/project scope."""


class AuthorizationError(CollaborationError, PermissionError):
	"""The actor is not allowed to perform a domain command."""


class InvalidTransitionError(ValidationError):
	"""A task state transition is not part of the frozen state machine."""
