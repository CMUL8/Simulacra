"""Recoverable coordinator for a conversation assignment spanning two stores.

There is intentionally no pretend cross-filesystem transaction here.  The
journal is the durable decision record and is the sole admission authority for
tagged children.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager, nullcontext
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from simulacra.collaboration.models import ConversationMessage, MessageAudit, Task, TaskState, validate_scope_id
from simulacra.operation_graph import OperationGraphStore
from simulacra.missions.models import clean_public_mapping
from simulacra.missions.repository import MissionConflictError
from simulacra.missions.service import _mint_assignment_admission


_STATES = frozenset({"PREPARED", "COMMIT_DECIDED", "STORES_DURABLE", "COMPLETE", "ABORTED"})
_OPERATION = "conversation_assignment"
_MAX_JOURNAL_BYTES = 1024 * 1024
_TEMP_JOURNAL_RE = re.compile(r"^\.(?P<request>[A-Za-z0-9_-]+)\.json\.(?P<pid>[0-9]+)\.(?P<nonce>[0-9]+)\.tmp$")
_ASSIGNMENT_ROLES = frozenset({"owner", "admin", "member", "reviewer", "approver"})


class AssignmentError(ValueError):
    """Stable client-safe assignment error."""


@dataclass(frozen=True, slots=True)
class AssignmentTransaction:
    transaction_id: str
    authenticated_human_actor_id: str
    operation: str
    client_request_id: str
    canonical_request_hash: str
    graph_revision: str
    reserved_message_id: str
    reserved_task_id: str
    reserved_run_id: str
    intended_payloads: dict[str, Any]
    state: str
    created_at: str
    updated_at: str

    def __post_init__(self) -> None:
        if self.state not in _STATES or self.operation != _OPERATION:
            raise AssignmentError("assignment_unavailable")
        try:
            for value in (self.transaction_id, self.authenticated_human_actor_id, self.client_request_id,
                          self.reserved_message_id, self.reserved_task_id, self.reserved_run_id):
                validate_scope_id(value, "assignment identifier")
        except (TypeError, ValueError) as exc:
            raise AssignmentError("assignment_unavailable") from exc

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AssignmentTransaction":
        try:
            expected = set(cls.__dataclass_fields__)
            if not isinstance(value, Mapping) or set(value) != expected:
                raise AssignmentError("assignment_unavailable")
            return cls(**dict(value))
        except (TypeError, ValueError) as exc:
            raise AssignmentError("assignment_unavailable") from exc


@dataclass(frozen=True, slots=True)
class AssignmentResult:
    transaction_id: str
    message_id: str
    task_id: str
    run_id: str
    state: str


class AssignmentCoordinator:
    def __init__(self, collaboration_repository: Any, mission_service: Any, workspace: str | Path,
                 *, runs_root: Path, clock: Callable[[], str], fault_injector: Callable[[str], None] | None = None) -> None:
        self.collaboration_repository = collaboration_repository
        self.mission_service = mission_service
        self.workspace = Path(workspace).resolve()
        self.runs_root = Path(os.path.abspath(os.fspath(runs_root)))
        if self.runs_root == Path("/"):
            raise AssignmentError("assignment_unavailable")
        root_fd = self._runs_root_fd(create=True)
        try:
            # The descriptor is the object we just validated.  Do not chmod the
            # path after closing it: an attacker could replace that path first.
            os.fchmod(root_fd, 0o700)
        finally:
            os.close(root_fd)
        self.clock = clock
        self.fault_injector = fault_injector

    @staticmethod
    def _canonical(value: Mapping[str, Any]) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    @staticmethod
    def _screen_string(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > 8000:
            raise AssignmentError("assignment_invalid")
        return value.strip()

    def _request(self, *, body: Any, title: Any, objective: Any, acceptance_criteria: Any,
                 assigned_agent_ids: Any, graph_revision: Any, reviewer_human_ids: Any = None,
                 source_message_id: Any = None) -> dict[str, Any]:
        if not isinstance(acceptance_criteria, list) or not isinstance(assigned_agent_ids, list):
            raise AssignmentError("assignment_invalid")
        if len(assigned_agent_ids) > 32:
            raise AssignmentError("assignment_invalid")
        agents: list[str] = []
        for agent in assigned_agent_ids:
            try:
                validate_scope_id(agent, "agent_id")
            except Exception as exc:
                raise AssignmentError("assignment_invalid") from exc
            agents.append(agent)
        if len(set(agents)) != len(agents):
            raise AssignmentError("assignment_invalid")
        criteria = [self._screen_string(item, "acceptance criterion") for item in acceptance_criteria]
        if not criteria or len(criteria) > 128:
            raise AssignmentError("assignment_invalid")
        reviewers = [] if reviewer_human_ids is None else reviewer_human_ids
        if not isinstance(reviewers, list) or len(reviewers) > 128:
            raise AssignmentError("assignment_invalid")
        clean_reviewers: list[str] = []
        for reviewer in reviewers:
            try:
                validate_scope_id(reviewer, "reviewer_human_id")
            except Exception as exc:
                raise AssignmentError("assignment_invalid") from exc
            clean_reviewers.append(reviewer)
        if len(set(clean_reviewers)) != len(clean_reviewers):
            raise AssignmentError("assignment_invalid")
        if source_message_id is not None:
            try:
                validate_scope_id(source_message_id, "source_message_id")
            except Exception as exc:
                raise AssignmentError("assignment_invalid") from exc
        request = {"body": self._screen_string(body, "body"), "title": self._screen_string(title, "title"),
                "objective": self._screen_string(objective, "objective"), "acceptance_criteria": criteria,
                "assigned_agent_ids": agents, "graph_revision": self._screen_string(graph_revision, "graph revision")}
        # Keep old persisted W1 assignment records verifiable.  New semantic
        # input becomes part of the replay hash only when it carries meaning.
        if clean_reviewers:
            request["reviewer_human_ids"] = clean_reviewers
        if source_message_id is not None:
            request["source_message_id"] = source_message_id
        try:
            clean_public_mapping(request)
        except Exception as exc:
            raise AssignmentError("assignment_invalid") from exc
        return request

    @staticmethod
    def _id(domain: str, identity: Mapping[str, str], request_hash: str) -> str:
        digest = hashlib.sha256((domain + "\0" + "\0".join(identity.values()) + "\0" + request_hash).encode()).hexdigest()
        return f"{domain}_{digest[:32]}"

    def _check_scope(self, tenant_id: str, project_id: str, actor: str, client_request_id: str,
                     *, error: str = "assignment_unavailable") -> None:
        try:
            for label, value in (("tenant_id", tenant_id), ("project_id", project_id), ("authenticated_human_actor_id", actor), ("client_request_id", client_request_id)):
                validate_scope_id(value, label)
        except (TypeError, ValueError) as exc:
            raise AssignmentError(error) from exc

    @staticmethod
    def _open_dir(parent: int | None, name: str, *, create: bool, private: bool = False) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            if not create:
                raise AssignmentError("assignment_unavailable")
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
                if parent is not None: os.fsync(parent)
            except FileExistsError:
                pass
            try:
                fd = os.open(name, flags, dir_fd=parent)
            except OSError as exc:
                raise AssignmentError("assignment_unavailable") from exc
        except OSError as exc:
            raise AssignmentError("assignment_unavailable") from exc
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            os.close(fd); raise AssignmentError("assignment_unavailable")
        if private:
            try:
                os.fchmod(fd, 0o700)
            except OSError as exc:
                os.close(fd)
                raise AssignmentError("assignment_unavailable") from exc
        return fd

    def _runs_root_fd(self, *, create: bool) -> int:
        """Open every configured root component from / with O_NOFOLLOW."""
        parts = self.runs_root.parts
        if not parts or parts[0] != "/" or len(parts) == 1:
            raise AssignmentError("assignment_unavailable")
        fd = self._open_dir(None, "/", create=False)
        try:
            for index, part in enumerate(parts[1:]):
                child = self._open_dir(
                    fd,
                    part,
                    create=create and index == len(parts[1:]) - 1,
                    private=index == len(parts[1:]) - 1,
                )
                os.close(fd); fd = child
            return fd
        except Exception:
            os.close(fd); raise

    def _project_dir(self, tenant: str, project: str, *, create: bool) -> int:
        root = self._runs_root_fd(create=False)
        try:
            control = self._open_dir(root, ".workplace-control", create=create, private=True); os.close(root)
            scoped_tenant = self._open_dir(control, tenant, create=create, private=True); os.close(control)
            scoped_project = self._open_dir(scoped_tenant, project, create=create, private=True); os.close(scoped_tenant)
            return scoped_project
        except Exception:
            try: os.close(root)
            except OSError: pass
            raise

    def _transaction_dir(self, project_fd: int, actor: str, *, create: bool) -> int:
        transactions = self._open_dir(project_fd, "assignment-transactions", create=create, private=True)
        actor_fd = -1
        try:
            actor_fd = self._open_dir(transactions, actor, create=create, private=True)
            operation = self._open_dir(actor_fd, _OPERATION, create=create, private=True)
            return operation
        finally:
            if actor_fd >= 0: os.close(actor_fd)
            os.close(transactions)

    def _existing_transaction_dir(self, project_fd: int, actor: str) -> int | None:
        """Open an existing actor operation directory without creating one."""
        def optional(parent: int, name: str) -> int | None:
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            except OSError as exc:
                raise AssignmentError("assignment_unavailable") from exc
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise AssignmentError("assignment_unavailable")
            return self._open_dir(parent, name, create=False, private=True)

        transactions = optional(project_fd, "assignment-transactions")
        if transactions is None:
            return None
        try:
            actor_fd = optional(transactions, actor)
            if actor_fd is None:
                return None
            try:
                return optional(actor_fd, _OPERATION)
            finally:
                os.close(actor_fd)
        finally:
            os.close(transactions)

    @contextmanager
    def _lock(self, tenant_id: str, project_id: str) -> Iterator[int]:
        self._check_scope(tenant_id, project_id, "lock", "lock")
        directory = self._project_dir(tenant_id, project_id, create=True)
        flags = os.O_RDWR | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            try:
                fd = os.open(".assignment-coordinator.lock", flags, dir_fd=directory)
            except FileNotFoundError:
                # O_NOFOLLOW|O_CREAT is not portable on macOS.  O_EXCL makes
                # first creation race-free; every existing leaf is reopened
                # with O_NOFOLLOW and fstat below.
                try:
                    fd = os.open(".assignment-coordinator.lock", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC, 0o600, dir_fd=directory)
                except FileExistsError:
                    fd = os.open(".assignment-coordinator.lock", flags, dir_fd=directory)
        except OSError as exc:
            os.close(directory); raise AssignmentError("assignment_unavailable") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise AssignmentError("assignment_unavailable")
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield directory
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            os.close(directory)

    def _checkpoint(self, stage: str) -> None:
        if self.fault_injector is not None:
            self.fault_injector(stage)

    def _read_leaf(self, directory: int, name: str) -> AssignmentTransaction | None:
        flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, dir_fd=directory)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise AssignmentError("assignment_unavailable") from exc
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode): raise AssignmentError("assignment_unavailable")
            if os.fstat(fd).st_size > 1024 * 1024:
                raise AssignmentError("assignment_unavailable")
            chunks: list[bytes] = []; total = 0
            while chunk := os.read(fd, 65536):
                total += len(chunk)
                if total > 1024 * 1024: raise AssignmentError("assignment_unavailable")
                chunks.append(chunk)
            return AssignmentTransaction.from_dict(json.loads(b"".join(chunks)))
        except (OSError, json.JSONDecodeError, TypeError, ValueError, AssignmentError) as exc:
            raise AssignmentError("assignment_unavailable") from exc
        finally:
            os.close(fd)

    def _read(self, directory: int, request: str) -> AssignmentTransaction | None:
        return self._read_leaf(directory, f"{request}.json")

    @staticmethod
    def _regular_leaf_exists(directory: int, name: str) -> bool:
        try:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise AssignmentError("assignment_unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise AssignmentError("assignment_unavailable")
        return True

    def _recover_temporary_journals(self, directory: int, tenant: str, project: str, actor: str) -> None:
        """Adopt only a fully validated coordinator temp left by hard process death."""
        temporary: list[tuple[str, str]] = []
        try:
            entries = list(os.scandir(directory))
        except OSError as exc:
            raise AssignmentError("assignment_unavailable") from exc
        for item in entries:
            name = item.name
            if name.endswith(".json"):
                request = name[:-5]
                try:
                    validate_scope_id(request, "client_request_id")
                except (TypeError, ValueError) as exc:
                    raise AssignmentError("assignment_unavailable") from exc
                if not item.is_file(follow_symlinks=False):
                    raise AssignmentError("assignment_unavailable")
                continue
            match = _TEMP_JOURNAL_RE.fullmatch(name)
            if match is None or not item.is_file(follow_symlinks=False):
                raise AssignmentError("assignment_unavailable")
            request = match.group("request")
            try:
                validate_scope_id(request, "client_request_id")
            except (TypeError, ValueError) as exc:
                raise AssignmentError("assignment_unavailable") from exc
            temporary.append((name, request))
        for name, request in sorted(temporary):
            try:
                tx = self._read_leaf(directory, name)
                if tx is None:  # A concurrent cleanup cannot become admission.
                    raise AssignmentError("assignment_unavailable")
                self._validate_intent(tx, tenant, project, actor, request)
            except AssignmentError:
                # This filename has already passed the narrow coordinator-owned
                # format. A torn or malformed temp was never published, so
                # discard it durably rather than letting a dead writer wedge
                # future recovery. Symlinks never reach this branch.
                try:
                    os.unlink(name, dir_fd=directory)
                    os.fsync(directory)
                except OSError as exc:
                    raise AssignmentError("assignment_unavailable") from exc
                continue
            canonical = f"{request}.json"
            if not self._regular_leaf_exists(directory, canonical):
                try:
                    os.replace(name, canonical, src_dir_fd=directory, dst_dir_fd=directory)
                    os.fsync(directory)
                except OSError as exc:
                    raise AssignmentError("assignment_unavailable") from exc
            else:
                try:
                    os.unlink(name, dir_fd=directory)
                    os.fsync(directory)
                except OSError as exc:
                    raise AssignmentError("assignment_unavailable") from exc

    def _write(self, directory: int, request: str, tx: AssignmentTransaction, label: str) -> AssignmentTransaction:
        name = f"{request}.json"
        temporary = f".{name}.{os.getpid()}.{id(tx)}.tmp"
        self._checkpoint(f"before_{label}_replace")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
        try:
            fd = os.open(temporary, flags, 0o600, dir_fd=directory)
        except OSError as exc:
            raise AssignmentError("assignment_unavailable") from exc
        try:
            payload = self._canonical(tx.to_dict())
            while payload:
                written = os.write(fd, payload)
                if written <= 0: raise AssignmentError("assignment_unavailable")
                payload = payload[written:]
            os.fsync(fd)
            self._checkpoint(f"after_{label}_temp_fsync")
            os.close(fd); fd = -1
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            self._checkpoint(f"after_{label}_replace")
            self._checkpoint(f"before_{label}_dir_fsync"); os.fsync(directory)
            self._checkpoint(f"after_{label}_dir_fsync")
            return tx
        except OSError as exc:
            raise AssignmentError("assignment_unavailable") from exc
        finally:
            if fd >= 0: os.close(fd)
            try: os.unlink(temporary, dir_fd=directory)
            except OSError: pass

    def _transition(self, directory: int, request: str, tx: AssignmentTransaction, state: str, label: str) -> AssignmentTransaction:
        updated = AssignmentTransaction(**{**tx.to_dict(), "state": state, "updated_at": self.clock()})
        return self._write(directory, request, updated, label)

    def _result(self, tx: AssignmentTransaction) -> AssignmentResult:
        return AssignmentResult(tx.transaction_id, tx.reserved_message_id, tx.reserved_task_id, tx.reserved_run_id, tx.state)

    def _require_assignment_authority(self, room: Any, actor: str) -> None:
        member = self.collaboration_repository.visible_member(room, actor)
        if member is None or member.role not in _ASSIGNMENT_ROLES:
            raise AssignmentError("assignment_unavailable")

    def _pin_graph(self, tenant: str, project: str, graph_revision: str):
        store = OperationGraphStore(self.workspace, tenant_id=tenant, project_id=project)
        return store, store.locked_current_approved_revision()

    def _write_message(self, tx: AssignmentTransaction, tenant: str, project: str) -> None:
        self._checkpoint("before_collaboration_message")
        intended = tx.intended_payloads["message"]
        def mutate(state: dict[str, Any]) -> None:
            current = state["messages"].get(tx.reserved_message_id)
            if current is None:
                state["messages"][tx.reserved_message_id] = intended
            elif current != intended:
                raise AssignmentError("assignment_unavailable")
        self.collaboration_repository.mutate_conversation_state(tenant, project, mutate)
        self._checkpoint("after_collaboration_message")

    def _write_task(self, tx: AssignmentTransaction, tenant: str, project: str) -> None:
        self._checkpoint("before_task")
        intended = tx.intended_payloads["task"]
        existing = next((task for task in self.collaboration_repository.list_tasks(tenant, project) if task.id == tx.reserved_task_id), None)
        if existing is None:
            self.collaboration_repository.create_task(Task.from_dict(intended))
        # A completed assignment remains retryable after humans move its task
        # through the normal collaboration lifecycle.  Its original identity
        # and linkage stay immutable; task ownership, state, revision, result,
        # and appended history belong to that later lifecycle.
        elif tx.state == "COMPLETE" and self._task_child_coherent(tx, existing):
            pass
        elif existing.to_dict() != intended:
            raise AssignmentError("assignment_unavailable")
        self._checkpoint("after_task")

    def _write_message_under_room_lock(self, tx: AssignmentTransaction, tenant: str, project: str) -> None:
        """Use the repository's existing state-replacement seam while its room lock is held."""
        self._checkpoint("before_collaboration_message")
        repository = self.collaboration_repository
        state = repository._load_conversation_state(tenant, project)
        current = state["messages"].get(tx.reserved_message_id)
        if current is None: state["messages"][tx.reserved_message_id] = tx.intended_payloads["message"]
        elif current != tx.intended_payloads["message"]: raise AssignmentError("assignment_unavailable")
        repository._replace_conversation_state(tenant, project, state)
        self._checkpoint("after_collaboration_message")

    def _write_task_under_room_lock(self, tx: AssignmentTransaction, tenant: str, project: str) -> None:
        self._checkpoint("before_task")
        repository = self.collaboration_repository
        path, rows = repository._collection(tenant, project, "tasks", create=True)
        current = rows.get(tx.reserved_task_id)
        intended = tx.intended_payloads["task"]
        if current is None:
            rows[tx.reserved_task_id] = intended
            repository._atomic_json(path, {key: rows[key] for key in sorted(rows)})
        elif current != intended:
            raise AssignmentError("assignment_unavailable")
        self._checkpoint("after_task")

    def _write_run(self, tx: AssignmentTransaction, tenant: str, project: str) -> None:
        self._checkpoint("before_pending_run")
        request = tx.intended_payloads["request"]
        self.mission_service.create_assignment_pending_run(
            tenant, project, run_id=tx.reserved_run_id, transaction_id=tx.transaction_id,
            trigger={"type": "conversation_assignment", "transaction_id": tx.transaction_id, "task_id": tx.reserved_task_id},
            graph_revision=tx.graph_revision, assigned_agent_ids=list(request["assigned_agent_ids"]),
        )
        self._checkpoint("after_pending_run")

    def _predecision_commit(self, directory: int, request_id: str, tx: AssignmentTransaction,
                            tenant: str, project: str, room: Any) -> AssignmentTransaction:
        """Authorize and publish PREPARED children under one room lock."""
        self._require_assignment_authority(room, tx.authenticated_human_actor_id)
        reviewers = tx.intended_payloads["request"].get("reviewer_human_ids", [])
        current_humans = {
            member.actor_id for member in self.collaboration_repository.visible_members(room)
        }
        if any(reviewer not in current_humans for reviewer in reviewers):
            raise AssignmentError("assignment_unavailable")
        self._write_message_under_room_lock(tx, tenant, project)
        self._write_task_under_room_lock(tx, tenant, project)
        self._write_run(tx, tenant, project)
        self._checkpoint("before_COMMIT_DECIDED")
        updated = self._transition(directory, request_id, tx, "COMMIT_DECIDED", "COMMIT_DECIDED")
        self._checkpoint("after_COMMIT_DECIDED")
        return updated

    def _children_coherent(self, tx: AssignmentTransaction, tenant: str, project: str) -> bool:
        """Read-only COMPLETE admission check; no partial child is public."""
        try:
            state = self.collaboration_repository.conversation_state(tenant, project)
            if not self._message_child_coherent(tx, state):
                return False
            task = self.collaboration_repository.get_task(tenant, project, tx.reserved_task_id)
            if not self._task_child_coherent(tx, task):
                return False
            run = next((item for item in self.mission_service.runs(tenant, project) if item.id == tx.reserved_run_id), None)
            if run is None:
                return False
            request = tx.intended_payloads["request"]
            return (
                run.tenant_id == tenant and run.project_id == project and run.assignment_transaction_id == tx.transaction_id
                and run.contract_revision == tx.graph_revision and run.assigned_agent_ids == request["assigned_agent_ids"]
                and run.trigger_snapshot == {"type": "conversation_assignment", "transaction_id": tx.transaction_id, "task_id": tx.reserved_task_id}
            )
        except Exception:
            return False

    @staticmethod
    def _task_child_coherent(tx: AssignmentTransaction, current: Task) -> bool:
        """Keep assignment facts fixed while allowing normal task progress.

        A completed task may be claimed, moved through work and review, and
        accrue its ordinary history.  Those changes must not be mistaken for a
        new assignment: its scope, source-message link, reviewer order, and
        original activity admission link remain exact.
        """
        try:
            intended = Task.from_dict(tx.intended_payloads["task"])
            immutable = (
                "id", "tenant_id", "project_id", "title", "objective",
                "acceptance_criteria", "source_message_id", "collaborator_ids",
                "operation_graph_version", "application_version", "schema_version", "created_at",
            )
            if any(getattr(current, field) != getattr(intended, field) for field in immutable):
                return False
            return bool(current.activity) and current.activity[0] == {"transaction_id": tx.transaction_id}
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _message_child_coherent(tx: AssignmentTransaction, state: Mapping[str, Any]) -> bool:
        """Accept only the original assignment message or its audited history.

        A linked assignment message may be edited or soft-deleted by its
        author.  Such a change is still public evidence only when the durable
        conversation audit proves the exact revision chain.  A bare record
        mutation remains a failed-closed tamper and cannot publish the work.
        """
        try:
            messages = state.get("messages")
            audits_raw = state.get("message_audits")
            initial_raw = tx.intended_payloads["message"]
            if not isinstance(messages, Mapping) or not isinstance(audits_raw, Mapping) or not isinstance(initial_raw, Mapping):
                return False
            current_raw = messages.get(tx.reserved_message_id)
            if not isinstance(current_raw, Mapping):
                return False
            initial = ConversationMessage.from_dict(initial_raw)
            current = ConversationMessage.from_dict(current_raw)
            if dict(current_raw) != current.to_dict():
                return False
            if current.to_dict() == initial.to_dict():
                return True
            if (
                current.id != initial.id or current.tenant_id != initial.tenant_id or current.project_id != initial.project_id
                or current.author != initial.author or current.kind != initial.kind or current.created_at != initial.created_at
                or current.root_message_id != initial.root_message_id or current.source_message_id != initial.source_message_id
                or current.links != initial.links or current.revision < 2
            ):
                return False
            audits: list[MessageAudit] = []
            for raw in audits_raw.values():
                if not isinstance(raw, Mapping):
                    return False
                audit = MessageAudit.from_dict(raw)
                if dict(raw) != audit.to_dict() or audit.message_id != initial.id:
                    continue
                audits.append(audit)
            audits.sort(key=lambda item: (item.prior_revision, item.occurred_at, item.id))
            if not audits:
                return False
            expected_revision, deleted, actor = 1, False, str(initial.author.get("id") or "")
            for index, audit in enumerate(audits):
                if (
                    deleted or audit.actor_id != actor or audit.prior_revision != expected_revision
                    or audit.resulting_revision != expected_revision + 1 or audit.prior_body is None
                ):
                    return False
                expected_revision = audit.resulting_revision
                if audit.operation == "delete":
                    deleted = True
                    if index != len(audits) - 1:
                        return False
            if current.revision != expected_revision or current.edited_at != audits[-1].occurred_at:
                return False
            if deleted:
                return current.body is None and current.deleted_at == audits[-1].occurred_at
            return isinstance(current.body, str) and current.deleted_at is None
        except Exception:
            return False

    def _validate_intent(self, tx: AssignmentTransaction, tenant: str, project: str, actor: str, request_id: str) -> None:
        """Treat the journal as untrusted bytes before using it to republish children."""
        try:
            if (tx.authenticated_human_actor_id != actor or tx.client_request_id != request_id or tx.operation != _OPERATION):
                raise AssignmentError("assignment_unavailable")
            raw = tx.intended_payloads.get("request") if isinstance(tx.intended_payloads, dict) else None
            if not isinstance(raw, dict):
                raise AssignmentError("assignment_unavailable")
            request = self._request(**raw)
            request_hash = hashlib.sha256(self._canonical(request)).hexdigest()
            identity = {"tenant": tenant, "project": project, "actor": actor, "operation": _OPERATION, "request": request_id}
            if (request != raw or request_hash != tx.canonical_request_hash or tx.graph_revision != request["graph_revision"]
                    or tx.transaction_id != self._id("txn", identity, request_hash)
                    or tx.reserved_message_id != self._id("msg", identity, request_hash)
                    or tx.reserved_task_id != self._id("task", identity, request_hash)
                    or tx.reserved_run_id != self._id("run", identity, request_hash)):
                raise AssignmentError("assignment_unavailable")
            message = ConversationMessage(
                id=tx.reserved_message_id, tenant_id=tenant, project_id=project,
                author={"id": actor, "kind": "human"}, kind="human_message", body=request["body"],
                created_at=tx.created_at, source_message_id=request.get("source_message_id"),
                links={"transaction_id": tx.transaction_id},
            ).to_dict()
            task = Task(
                id=tx.reserved_task_id, tenant_id=tenant, project_id=project, title=request["title"],
                objective=request["objective"], acceptance_criteria=list(request["acceptance_criteria"]),
                source_message_id=tx.reserved_message_id,
                collaborator_ids=list(request.get("reviewer_human_ids", [])),
                activity=[{"transaction_id": tx.transaction_id}],
                created_at=tx.created_at, updated_at=tx.created_at,
            ).to_dict()
        except AssignmentError:
            raise
        except (TypeError, ValueError, KeyError) as exc:
            raise AssignmentError("assignment_unavailable") from exc
        if tx.intended_payloads.get("message") != message or tx.intended_payloads.get("task") != task:
            raise AssignmentError("assignment_unavailable")

    def _revalidate_and_complete(self, directory: int, request_id: str, tx: AssignmentTransaction,
                                 tenant: str, project: str, actor: str, *, pinned_approved: Any | None = None,
                                 locked_room: Any | None = None, stop_after_decision: bool = False) -> AssignmentResult:
        if tx.state == "ABORTED":
            raise AssignmentError("transaction_aborted")
        try:
            self._validate_intent(tx, tenant, project, actor, request_id)
        except AssignmentError:
            if tx.state == "PREPARED":
                self._transition(directory, request_id, tx, "ABORTED", "ABORTED")
                raise AssignmentError("transaction_aborted")
            raise AssignmentError("assignment_unavailable")
        # The graph head and membership are mutable pre-decision admission
        # checks only.  A durable decision must finish after later changes.
        if tx.state == "PREPARED":
            graph_lock = (
                nullcontext(pinned_approved)
                if pinned_approved is not None
                else OperationGraphStore(self.workspace, tenant_id=tenant, project_id=project).locked_current_approved_revision()
            )
            with graph_lock as current:
                if current is None or current.revision_hash != tx.graph_revision:
                    self._transition(directory, request_id, tx, "ABORTED", "ABORTED")
                    raise AssignmentError("transaction_aborted")
                try:
                    if locked_room is None:
                        # Keep authority and all pre-decision writes under one
                        # repository room lock so role changes cannot interleave.
                        with self.collaboration_repository.room_lock(tenant, project) as room:
                            tx = self._predecision_commit(directory, request_id, tx, tenant, project, room)
                    else:
                        tx = self._predecision_commit(directory, request_id, tx, tenant, project, locked_room)
                except (AssignmentError, MissionConflictError, TypeError, ValueError, OSError) as exc:
                    self._transition(directory, request_id, tx, "ABORTED", "ABORTED")
                    raise AssignmentError("transaction_aborted") from exc
        if stop_after_decision and tx.state == "COMMIT_DECIDED":
            return self._result(tx)
        # The approval lock was held through all pre-decision child writes.
        # After decision only immutable intent validation governs recovery.
        if tx.state in {"COMMIT_DECIDED", "STORES_DURABLE", "COMPLETE"}:
            # Retain the global order after decision too, but deliberately do
            # not consult the mutable current head or room membership again.
            postdecision_graph_lock = (
                nullcontext(pinned_approved)
                if pinned_approved is not None
                else OperationGraphStore(self.workspace, tenant_id=tenant, project_id=project).locked_current_approved_revision()
            )
            with postdecision_graph_lock:
                try:
                    self._write_message(tx, tenant, project)
                    self._write_task(tx, tenant, project)
                    self._write_run(tx, tenant, project)
                except (MissionConflictError, TypeError, ValueError, OSError) as exc:
                    raise AssignmentError("assignment_unavailable") from exc
                if tx.state == "COMMIT_DECIDED":
                    self._checkpoint("before_STORES_DURABLE")
                    tx = self._transition(directory, request_id, tx, "STORES_DURABLE", "STORES_DURABLE")
                    self._checkpoint("after_STORES_DURABLE")
                if tx.state == "STORES_DURABLE":
                    self.mission_service.activate_assignment_run(tenant, project, run_id=tx.reserved_run_id, transaction_id=tx.transaction_id)
                    self._checkpoint("after_queued_before_COMPLETE")
                    self._checkpoint("before_COMPLETE")
                    tx = self._transition(directory, request_id, tx, "COMPLETE", "COMPLETE")
                    self._checkpoint("after_COMPLETE")
                elif tx.state == "COMPLETE":
                    self.mission_service.activate_assignment_run(tenant, project, run_id=tx.reserved_run_id, transaction_id=tx.transaction_id)
                if not self._children_coherent(tx, tenant, project):
                    raise AssignmentError("assignment_unavailable")
        return self._result(tx)

    def assign(self, *, tenant_id: str, project_id: str, authenticated_human_actor_id: str, client_request_id: str,
               body: Any, title: Any, objective: Any, acceptance_criteria: Any, assigned_agent_ids: Any,
               graph_revision: Any, reviewer_human_ids: Any = None, source_message_id: Any = None) -> AssignmentResult:
        self._check_scope(tenant_id, project_id, authenticated_human_actor_id, client_request_id, error="assignment_invalid")
        request = self._request(body=body, title=title, objective=objective, acceptance_criteria=acceptance_criteria,
                                assigned_agent_ids=assigned_agent_ids, graph_revision=graph_revision,
                                reviewer_human_ids=reviewer_human_ids, source_message_id=source_message_id)
        request_hash = hashlib.sha256(self._canonical(request)).hexdigest()
        identity = {"tenant": tenant_id, "project": project_id, "actor": authenticated_human_actor_id,
                    "operation": _OPERATION, "request": client_request_id}
        with self._lock(tenant_id, project_id) as project_fd:
            directory = self._existing_transaction_dir(project_fd, authenticated_human_actor_id)
            if directory is not None:
                try:
                    self._recover_temporary_journals(directory, tenant_id, project_id, authenticated_human_actor_id)
                    tx = self._read(directory, client_request_id)
                    if tx is not None:
                        if tx.canonical_request_hash != request_hash:
                            raise AssignmentError("idempotency_mismatch")
                        return self._revalidate_and_complete(
                            directory, client_request_id, tx, tenant_id, project_id, authenticated_human_actor_id,
                        )
                finally:
                    os.close(directory)

            # A new intent does not create an actor transaction directory until
            # its exact requested revision is pinned as the current approval.
            store = OperationGraphStore(self.workspace, tenant_id=tenant_id, project_id=project_id)
            with store.locked_current_approved_revision() as current:
                if current is None or current.revision_hash != request["graph_revision"]:
                    raise AssignmentError("assignment_unavailable")
                with self.collaboration_repository.room_lock(tenant_id, project_id) as room:
                    self._require_assignment_authority(room, authenticated_human_actor_id)
                    directory = self._transaction_dir(project_fd, authenticated_human_actor_id, create=True)
                    try:
                        stamp = self.clock()
                        transaction_id = self._id("txn", identity, request_hash)
                        message = ConversationMessage(
                            id=self._id("msg", identity, request_hash), tenant_id=tenant_id, project_id=project_id,
                            author={"id": authenticated_human_actor_id, "kind": "human"}, kind="human_message", body=request["body"],
                            created_at=stamp, source_message_id=request.get("source_message_id"), links={"transaction_id": transaction_id},
                        ).to_dict()
                        task = Task(
                            id=self._id("task", identity, request_hash), tenant_id=tenant_id, project_id=project_id,
                            title=request["title"], objective=request["objective"], acceptance_criteria=list(request["acceptance_criteria"]),
                            source_message_id=message["id"], collaborator_ids=list(request.get("reviewer_human_ids", [])),
                            activity=[{"transaction_id": transaction_id}],
                            created_at=stamp, updated_at=stamp,
                        ).to_dict()
                        tx = AssignmentTransaction(transaction_id, authenticated_human_actor_id, _OPERATION, client_request_id,
                            request_hash, request["graph_revision"], message["id"], task["id"], self._id("run", identity, request_hash),
                            {"request": request, "message": message, "task": task}, "PREPARED", stamp, stamp)
                        if len(self._canonical(tx.to_dict())) > _MAX_JOURNAL_BYTES:
                            raise AssignmentError("assignment_invalid")
                        self._write(directory, client_request_id, tx, "PREPARED")
                        self._revalidate_and_complete(
                            directory, client_request_id, tx, tenant_id, project_id, authenticated_human_actor_id,
                            pinned_approved=current, locked_room=room, stop_after_decision=True,
                        )
                    finally:
                        os.close(directory)
                directory = self._transaction_dir(project_fd, authenticated_human_actor_id, create=False)
                try:
                    committed = self._read(directory, client_request_id)
                    if committed is None:
                        raise AssignmentError("assignment_unavailable")
                    return self._revalidate_and_complete(
                        directory, client_request_id, committed, tenant_id, project_id, authenticated_human_actor_id,
                        pinned_approved=current,
                    )
                finally:
                    os.close(directory)

    def replay_if_exists(self, *, tenant_id: str, project_id: str, authenticated_human_actor_id: str,
                         client_request_id: str, body: Any, title: Any, objective: Any,
                         acceptance_criteria: Any, assigned_agent_ids: Any,
                         reviewer_human_ids: Any = None, source_message_id: Any = None) -> AssignmentResult | None:
        """Finish and return a matching durable assignment without re-admitting it.

        Route callers use this before reading mutable crew or plan state.  A
        lost response must recover the same committed work even after those
        unrelated current-state details have changed; mismatched public input
        still fails as an idempotency conflict.
        """
        self._check_scope(tenant_id, project_id, authenticated_human_actor_id, client_request_id, error="assignment_invalid")
        with self._lock(tenant_id, project_id) as project_fd:
            directory = self._existing_transaction_dir(project_fd, authenticated_human_actor_id)
            if directory is None:
                return None
            try:
                self._recover_temporary_journals(directory, tenant_id, project_id, authenticated_human_actor_id)
                tx = self._read(directory, client_request_id)
                if tx is None:
                    return None
                request = self._request(
                    body=body, title=title, objective=objective, acceptance_criteria=acceptance_criteria,
                    assigned_agent_ids=assigned_agent_ids, graph_revision=tx.graph_revision,
                    reviewer_human_ids=reviewer_human_ids, source_message_id=source_message_id,
                )
                if hashlib.sha256(self._canonical(request)).hexdigest() != tx.canonical_request_hash:
                    raise AssignmentError("idempotency_mismatch")
                return self._revalidate_and_complete(
                    directory, client_request_id, tx, tenant_id, project_id, authenticated_human_actor_id,
                )
            finally:
                os.close(directory)

    def recover(self, *, tenant_id: str, project_id: str, authenticated_human_actor_id: str, client_request_id: str) -> AssignmentResult:
        self._check_scope(tenant_id, project_id, authenticated_human_actor_id, client_request_id)
        with self._lock(tenant_id, project_id) as project_fd:
            directory = self._transaction_dir(project_fd, authenticated_human_actor_id, create=False)
            self._recover_temporary_journals(directory, tenant_id, project_id, authenticated_human_actor_id)
            tx = self._read(directory, client_request_id)
            if tx is None:
                os.close(directory)
                raise AssignmentError("transaction_aborted")
            try: return self._revalidate_and_complete(directory, client_request_id, tx, tenant_id, project_id, authenticated_human_actor_id)
            finally: os.close(directory)

    def recover_project(self, tenant_id: str, project_id: str) -> list[AssignmentResult]:
        self._check_scope(tenant_id, project_id, "lock", "lock")
        with self._lock(tenant_id, project_id) as project_fd:
            try:
                info = os.stat("assignment-transactions", dir_fd=project_fd, follow_symlinks=False)
            except FileNotFoundError:
                return []
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise AssignmentError("assignment_unavailable")
            transactions = self._open_dir(project_fd, "assignment-transactions", create=False, private=True)
            try:
                actor_names: list[str] = []
                for actor_entry in os.scandir(transactions):
                    try:
                        validate_scope_id(actor_entry.name, "actor_id")
                    except (TypeError, ValueError) as exc:
                        raise AssignmentError("assignment_unavailable") from exc
                    if not actor_entry.is_dir(follow_symlinks=False): raise AssignmentError("assignment_unavailable")
                    actor_names.append(actor_entry.name)
                results: list[AssignmentResult] = []
                for actor in sorted(actor_names):
                    actor_fd = self._open_dir(transactions, actor, create=False, private=True)
                    directory = self._open_dir(actor_fd, _OPERATION, create=False, private=True); os.close(actor_fd)
                    try:
                        self._recover_temporary_journals(directory, tenant_id, project_id, actor)
                        requests: list[str] = []
                        for item in os.scandir(directory):
                            if not item.name.endswith(".json") or not item.is_file(follow_symlinks=False): raise AssignmentError("assignment_unavailable")
                            request = item.name[:-5]
                            try:
                                validate_scope_id(request, "client_request_id")
                            except (TypeError, ValueError) as exc:
                                raise AssignmentError("assignment_unavailable") from exc
                            requests.append(request)
                        for request in sorted(requests):
                            try:
                                tx = self._read(directory, request)
                                if tx is not None:
                                    self._validate_intent(tx, tenant_id, project_id, actor, request)
                                if tx is not None and tx.state not in {"COMPLETE", "ABORTED"}:
                                    results.append(self._revalidate_and_complete(directory, request, tx, tenant_id, project_id, actor))
                            except AssignmentError as exc:
                                if str(exc) != "transaction_aborted": raise
                    finally: os.close(directory)
                return results
            finally:
                os.close(transactions)

    def project_agent_results(self, tenant_id: str, project_id: str) -> list[str]:
        """Repair the shared Conversation from durable agent milestones."""
        self._check_scope(tenant_id, project_id, "projector", "projector")
        from simulacra.collaboration import CollaborationService

        runs = {run.id: run for run in self.mission_service.runs(tenant_id, project_id)}
        agents = {agent.id: agent for agent in self.mission_service.agents(tenant_id, project_id)}
        outputs = self.mission_service.deliverables(tenant_id, project_id)
        conversation = CollaborationService(self.collaboration_repository)
        projected: list[str] = []
        for event in self.mission_service.events(tenant_id, project_id):
            event_type = event.get("type")
            if event_type not in {"agent_started", "agent_completed", "agent_failed"}:
                continue
            event_id, run_id = event.get("id"), event.get("run_id")
            payload = event.get("payload") if isinstance(event.get("payload"), Mapping) else {}
            agent_id = payload.get("agent_id")
            if not all(isinstance(value, str) and value for value in (event_id, run_id, agent_id)):
                continue
            run = runs.get(run_id)
            if run is None or agent_id not in agents or (
                run.assigned_agent_ids and agent_id not in run.assigned_agent_ids
            ):
                continue
            trigger = run.trigger_snapshot if isinstance(run.trigger_snapshot, Mapping) else {}
            task_id = trigger.get("task_id")
            work_item_id = task_id if isinstance(task_id, str) and task_id else run.id
            created_at = str(event.get("timestamp") or run.updated_at)
            if event_type == "agent_started":
                message = conversation.project_agent_progress(
                    tenant_id=tenant_id, project_id=project_id, source_event_id=event_id,
                    agent_id=agent_id,
                    body="Working on the assignment. Progress and questions will return here.",
                    created_at=created_at, work_item_id=work_item_id, run_id=run.id,
                )
                projected.append(message.id)
                continue
            if event_type == "agent_failed":
                message = conversation.project_agent_failure(
                    tenant_id=tenant_id, project_id=project_id, source_event_id=event_id,
                    agent_id=agent_id,
                    body="Work stopped before completion. Review it in Work before continuing.",
                    created_at=created_at, work_item_id=work_item_id, run_id=run.id,
                )
                projected.append(message.id)
                continue
            linked_outputs = sorted(
                (
                    item for item in outputs
                    if item.producer_id == agent_id and any(
                        isinstance(evidence, Mapping) and evidence.get("run_id") == run_id
                        for evidence in item.validation_evidence
                    )
                ),
                key=lambda item: (item.created_at, item.id),
            )
            output_id = linked_outputs[0].id if linked_outputs else None
            # Provider prose is execution evidence, not public product copy.
            # Task titles and artifact filenames may also contain agent-chosen
            # text, so the shared room gets fixed product copy plus opaque links.
            response = (
                "Work completed. An output is ready for human verification."
                if linked_outputs else "Work completed. Review the result in Work."
            )
            message = conversation.project_agent_completion(
                tenant_id=tenant_id, project_id=project_id, source_event_id=event_id,
                agent_id=agent_id, body=response, created_at=created_at,
                work_item_id=work_item_id, run_id=run.id, output_id=output_id,
            )
            projected.append(message.id)
        return projected

    @contextmanager
    def project_claim_guard(self, tenant_id: str, project_id: str) -> Iterator[object]:
        with self._lock(tenant_id, project_id) as project_fd:
            # Snapshot before MissionService acquires its mutation lock.  The
            # resulting capability performs no filesystem/repository I/O,
            # preventing a Mission -> collaboration lock inversion in claim.
            allowed = self._complete_pairs(project_fd, tenant_id, project_id)
            yield _mint_assignment_admission(lambda transaction_id, run_id: (transaction_id, run_id) in allowed)

    def _complete_pairs(self, project_fd: int, tenant: str, project: str) -> frozenset[tuple[str, str]]:
        pairs: set[tuple[str, str]] = set()
        try:
            transactions = self._open_dir(project_fd, "assignment-transactions", create=False, private=True)
            try:
                for actor in os.scandir(transactions):
                    try:
                        validate_scope_id(actor.name, "actor_id")
                    except (TypeError, ValueError):
                        return frozenset()
                    if not actor.is_dir(follow_symlinks=False): return frozenset()
                    actor_fd = self._open_dir(transactions, actor.name, create=False, private=True)
                    operation = self._open_dir(actor_fd, _OPERATION, create=False, private=True); os.close(actor_fd)
                    try:
                        self._recover_temporary_journals(operation, tenant, project, actor.name)
                        for item in os.scandir(operation):
                            if not item.name.endswith(".json") or not item.is_file(follow_symlinks=False): return frozenset()
                            request = item.name[:-5]
                            try:
                                validate_scope_id(request, "client_request_id")
                            except (TypeError, ValueError):
                                return frozenset()
                            tx = self._read(operation, request)
                            if tx is not None and tx.state == "COMPLETE":
                                self._validate_intent(tx, tenant, project, actor.name, request)
                                if not self._children_coherent(tx, tenant, project): return frozenset()
                                pairs.add((tx.transaction_id, tx.reserved_run_id))
                    finally: os.close(operation)
            finally: os.close(transactions)
        except (AssignmentError, OSError):
            return frozenset()
        return frozenset(pairs)

    def _is_complete(self, project_fd: int, transaction_id: str, run_id: str | None = None,
                     tenant: str | None = None, project: str | None = None) -> bool:
        try:
            validate_scope_id(transaction_id, "transaction_id")
            transactions = self._open_dir(project_fd, "assignment-transactions", create=False, private=True)
            try:
                for actor in os.scandir(transactions):
                    try:
                        validate_scope_id(actor.name, "actor_id")
                    except (TypeError, ValueError):
                        return False
                    if not actor.is_dir(follow_symlinks=False): return False
                    actor_fd = self._open_dir(transactions, actor.name, create=False, private=True)
                    operation = self._open_dir(actor_fd, _OPERATION, create=False, private=True); os.close(actor_fd)
                    try:
                        if tenant is None or project is None:
                            return False
                        self._recover_temporary_journals(operation, tenant, project, actor.name)
                        for item in os.scandir(operation):
                            if not item.name.endswith(".json") or not item.is_file(follow_symlinks=False): return False
                            request = item.name[:-5]
                            try:
                                validate_scope_id(request, "client_request_id")
                            except (TypeError, ValueError):
                                return False
                            tx = self._read(operation, request)
                            if tx and tx.transaction_id == transaction_id:
                                self._validate_intent(tx, tenant, project, actor.name, request)
                                if tx.state != "COMPLETE" or (run_id is not None and tx.reserved_run_id != run_id): return False
                                return tenant is not None and project is not None and self._children_coherent(tx, tenant, project)
                    finally: os.close(operation)
            finally: os.close(transactions)
        except (AssignmentError, OSError, TypeError, ValueError):
            return False
        return False

    def visible_result(self, *, tenant_id: str, project_id: str, transaction_id: str) -> AssignmentResult | None:
        """Projection seam for W2/W3: tagged children stay hidden until COMPLETE."""
        try:
            with self._lock(tenant_id, project_id) as project_fd:
                if not self._is_complete(project_fd, transaction_id, tenant=tenant_id, project=project_id): return None
                transactions = self._open_dir(project_fd, "assignment-transactions", create=False, private=True)
                try:
                    for actor in os.scandir(transactions):
                        try:
                            validate_scope_id(actor.name, "actor_id")
                        except (TypeError, ValueError):
                            return None
                        actor_fd = self._open_dir(transactions, actor.name, create=False, private=True)
                        operation = self._open_dir(actor_fd, _OPERATION, create=False, private=True); os.close(actor_fd)
                        try:
                            self._recover_temporary_journals(operation, tenant_id, project_id, actor.name)
                            for item in os.scandir(operation):
                                if not item.name.endswith(".json") or not item.is_file(follow_symlinks=False):
                                    return None
                                request = item.name[:-5]
                                try:
                                    validate_scope_id(request, "client_request_id")
                                except (TypeError, ValueError):
                                    return None
                                tx = self._read(operation, request)
                                if tx and tx.transaction_id == transaction_id:
                                    self._validate_intent(tx, tenant_id, project_id, actor.name, request)
                                    return self._result(tx)
                        finally: os.close(operation)
                finally: os.close(transactions)
        except (AssignmentError, OSError):
            return None
        return None
