"""Typed models for session/memory replay consistency tests.

The replay harness uses a small protocol instead of hard-coding backend calls
inside each test. This keeps the tests readable and makes it easy to add new
backends or new replay cases over time.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from enum import Enum
from typing import Any
from typing import Optional


class ReplayStepKind(str, Enum):
    """Supported operations in a replay case."""

    CREATE_SESSION = "create_session"
    APPEND_EVENT = "append_event"
    APPEND_STATE = "append_state"
    STORE_MEMORY = "store_memory"
    SEARCH_MEMORY = "search_memory"
    CREATE_SUMMARY = "create_summary"
    RESTART_SERVICES = "restart_services"


class SnapshotMutationOperation(str, Enum):
    """Supported snapshot mutation operations for negative cases."""

    SET = "set"
    DELETE = "delete"


class RuntimeFaultOperation(str, Enum):
    """Supported runtime fault injections for negative replay cases."""

    DUPLICATE_LAST_EVENT = "duplicate_last_event"
    DROP_LAST_EVENT_KEEP_STATE = "drop_last_event_keep_state"
    SET_SESSION_VALUE = "set_session_value"
    DELETE_SUMMARY = "delete_summary"
    SET_SUMMARY_VALUE = "set_summary_value"


@dataclass(frozen=True)
class FunctionCallSpec:
    """Declarative function call content for an event."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None


@dataclass(frozen=True)
class FunctionResponseSpec:
    """Declarative function response content for an event."""

    name: str
    response: dict[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None


@dataclass(frozen=True)
class EventSpec:
    """Business-level event description used by replay steps."""

    author: str
    text: Optional[str] = None
    role: Optional[str] = None
    state_delta: dict[str, Any] = field(default_factory=dict)
    function_calls: tuple[FunctionCallSpec, ...] = field(default_factory=tuple)
    function_responses: tuple[FunctionResponseSpec, ...] = field(default_factory=tuple)
    branch: Optional[str] = None
    visible: bool = True
    partial: bool = False
    is_summary_event: bool = False
    event_id: Optional[str] = None


@dataclass(frozen=True)
class MemoryQuerySpec:
    """Search query to run after replaying a case."""

    name: str
    query: str
    limit: int = 10


@dataclass(frozen=True)
class ReplayStep:
    """One replay operation."""

    kind: ReplayStepKind
    event: Optional[EventSpec] = None
    initial_state: dict[str, Any] = field(default_factory=dict)
    memory_query: Optional[MemoryQuerySpec] = None
    force_summary: bool = False
    session_alias: str = "default"
    session_id: Optional[str] = None

    @classmethod
    def create_session(
        cls,
        *,
        initial_state: Optional[dict[str, Any]] = None,
        session_alias: str = "default",
        session_id: Optional[str] = None,
    ) -> "ReplayStep":
        return cls(
            kind=ReplayStepKind.CREATE_SESSION,
            initial_state=initial_state or {},
            session_alias=session_alias,
            session_id=session_id,
        )

    @classmethod
    def append_event(cls, event: EventSpec, *, session_alias: str = "default") -> "ReplayStep":
        return cls(kind=ReplayStepKind.APPEND_EVENT, event=event, session_alias=session_alias)

    @classmethod
    def append_state(
        cls,
        *,
        author: str,
        state_delta: dict[str, Any],
        session_alias: str = "default",
    ) -> "ReplayStep":
        return cls(
            kind=ReplayStepKind.APPEND_STATE,
            event=EventSpec(author=author, state_delta=state_delta),
            session_alias=session_alias,
        )

    @classmethod
    def store_memory(cls, *, session_alias: str = "default") -> "ReplayStep":
        return cls(kind=ReplayStepKind.STORE_MEMORY, session_alias=session_alias)

    @classmethod
    def search_memory(
        cls,
        *,
        name: str,
        query: str,
        limit: int = 10,
        session_alias: str = "default",
    ) -> "ReplayStep":
        return cls(
            kind=ReplayStepKind.SEARCH_MEMORY,
            memory_query=MemoryQuerySpec(name=name, query=query, limit=limit),
            session_alias=session_alias,
        )

    @classmethod
    def create_summary(cls, *, force: bool = False, session_alias: str = "default") -> "ReplayStep":
        return cls(kind=ReplayStepKind.CREATE_SUMMARY, force_summary=force, session_alias=session_alias)

    @classmethod
    def restart_services(cls) -> "ReplayStep":
        return cls(kind=ReplayStepKind.RESTART_SERVICES)


@dataclass(frozen=True)
class ReplayCase:
    """A deterministic replay scenario."""

    case_id: str
    description: str
    app_name: str = "replay_app"
    user_id: str = "replay_user"
    session_id: str = "replay_session"
    enable_summary: bool = False
    summary_keep_recent_count: int = 2
    store_historical_events: bool = False
    steps: tuple[ReplayStep, ...] = field(default_factory=tuple)
    allowed_diff_paths: tuple[str, ...] = field(default_factory=tuple)
    expected_diff_paths: tuple[str, ...] = field(default_factory=tuple)
    snapshot_mutations: tuple["SnapshotMutation", ...] = field(default_factory=tuple)
    runtime_faults: tuple["RuntimeFault", ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SnapshotMutation:
    """A deterministic post-replay mutation applied to one backend snapshot."""

    backend_name: str
    path: str
    operation: SnapshotMutationOperation = SnapshotMutationOperation.SET
    value: Any = None


@dataclass(frozen=True)
class RuntimeFault:
    """A deterministic fault injected during replay execution."""

    backend_name: str
    after_step: int
    operation: RuntimeFaultOperation
    path: Optional[str] = None
    value: Any = None


@dataclass(frozen=True)
class SummarySnapshot:
    """Comparable summary view extracted from a backend."""

    session_id: str
    summary_text: str
    original_event_count: int
    compressed_event_count: int
    summary_id: Optional[str] = None
    version: Optional[int] = None
    replaces: Optional[str] = None
    summarized_event_count: Optional[int] = None
    summary_timestamp: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BackendSnapshot:
    """Full business snapshot collected after replaying one case."""

    backend_name: str
    case_id: str
    app_name: str
    user_id: str
    session_id: str
    session: dict[str, Any]
    state: dict[str, Any]
    memory: dict[str, Any]
    summary: Optional[SummarySnapshot]

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.summary is not None:
            data["summary"] = self.summary.to_dict()
        return data


@dataclass(frozen=True)
class DiffEntry:
    """One structured difference between two backend snapshots."""

    case_id: str
    backend_a: str
    backend_b: str
    scope: str
    path: str
    left: Any
    right: Any
    allowed: bool
    session_id: Optional[str] = None
    event_index: Optional[int] = None
    summary_id: Optional[str] = None
    reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
