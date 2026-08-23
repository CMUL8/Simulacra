"""Runtime-plane domain errors."""


class RuntimePlaneError(Exception):
	"""Base class for runtime failures."""


class RuntimeScopeError(RuntimePlaneError, ValueError):
	pass


class RuntimeConflictError(RuntimePlaneError):
	pass


class RuntimeNotFoundError(RuntimePlaneError):
	pass


class RuntimeAuthorizationError(RuntimePlaneError):
	pass


class InvalidTransitionError(RuntimePlaneError):
	pass


class ApprovalRequiredError(RuntimePlaneError):
	pass


class ActionExecutionError(RuntimePlaneError):
	pass
