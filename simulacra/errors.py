class PrimeAgentError(Exception):
	"""Base error for the Simulacra / Prime Agent wrapper."""


class StartupError(PrimeAgentError):
	"""Raised when the Prime Agent process fails to start."""


class RpcError(PrimeAgentError):
	"""Raised when an RPC command fails or returns success=false."""


class TimeoutError(PrimeAgentError):
	"""Raised when waiting for the agent exceeds the configured timeout."""
