from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ValidationIssue:
	path: str
	message: str

	def __str__(self) -> str:
		return f"{self.path}: {self.message}"


class OperationGraphError(Exception):
	"""Base error for the Operation Graph contract."""


class GraphValidationError(OperationGraphError):
	def __init__(self, issues: list[ValidationIssue] | tuple[ValidationIssue, ...]):
		self.issues = tuple(issues)
		super().__init__("Operation Graph validation failed:\n" + "\n".join(f"- {issue}" for issue in self.issues))


class GraphParseError(OperationGraphError):
	"""The serialized graph could not be parsed."""


class CredentialPolicyError(OperationGraphError, ValueError):
	"""Connector configuration includes a raw credential rather than a reference."""


class RevisionConflictError(OperationGraphError):
	"""The expected head revision does not match the persisted head."""


class RevisionNotFoundError(OperationGraphError):
	"""The requested immutable revision does not exist in this project."""


class UnapprovedRevisionError(OperationGraphError):
	"""A consumer requested a revision that has not been approved exactly."""
