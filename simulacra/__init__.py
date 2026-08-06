"""Simulacra — Python wrapper for Prime Agent (RPC mode)."""

from .agent import Agent, AgentResult, RunOptions
from .errors import PrimeAgentError, RpcError, StartupError, TimeoutError
from .pool import AgentPool, PoolResult, PoolTask
from .resolve import resolve_prime_agent

__all__ = [
	"Agent",
	"AgentPool",
	"AgentResult",
	"PoolResult",
	"PoolTask",
	"PrimeAgentError",
	"RpcError",
	"RunOptions",
	"StartupError",
	"TimeoutError",
	"resolve_prime_agent",
]

__version__ = "0.1.0"
