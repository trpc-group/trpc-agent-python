# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared fixtures and utilities for Session/Memory replay consistency tests.

This module provides:
- ReplayCase / ReplayStep: data models describing a replay scenario trajectory
- load_replay_case(): loads a replay scenario from a JSONL file
- make_inmemory_service() / make_sqlite_service(): backend factory functions
- normalize_*(): normalization helpers for cross-backend comparison
- backend_pair: pytest fixture yielding (InMemory, SQLite) in lightweight mode
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from trpc_agent_sdk.abc import MemoryServiceConfig, SearchMemoryResponse
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.sessions import (
    InMemorySessionService,
    Session,
    SessionServiceConfig,
    SessionSummarizer,
    SessionSummary,
    SqlSessionService,
    SummarizerSessionManager,
)
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content, Part

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPLAY_CASES_DIR = Path(__file__).parent / "replay_cases"

# Default test config: no TTL, no event count limit
_DEFAULT_SESSION_CONFIG = SessionServiceConfig()
_DEFAULT_SESSION_CONFIG.clean_ttl_config()

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------


@dataclass
class ReplayStep:
    """A single step in a replay case trajectory.

    Fields:
        action: The operation to perform, e.g. ``create_session``,
            ``append_event``, ``get_session``, ``update_state``,
            ``store_memory``, ``search_memory``, ``create_session_summary``,
            ``get_session_summary``.
        kwargs: Keyword arguments passed to the backend method.
        expected: Optional dict of expected outcomes for verification.
        expected_events: Optional int, shorthand for expected event count.
        inject: Optional dict describing an injected inconsistency
            (only used in injected cases).
    """

    action: str = ""
    kwargs: Dict[str, Any] = field(default_factory=dict)
    expected: Optional[Dict[str, Any]] = None
    expected_events: Optional[int] = None
    inject: Optional[Dict[str, Any]] = None


@dataclass
class ReplayCase:
    """A complete replay scenario covering one or more session operations.

    Fields:
        name: Unique case identifier, e.g. ``case_01_single_turn``.
        description: Human-readable description of the scenario.
        steps: Ordered list of ReplayStep instances to drive the backends.
    """

    name: str = ""
    description: str = ""
    steps: List[ReplayStep] = field(default_factory=list)


def load_replay_case(name: str) -> ReplayCase:
    """Load a replay case from a JSONL file in ``replay_cases/``.

    The JSONL format:
      - Line 1: metadata dict with ``name`` and ``description`` keys.
      - Lines 2+: one JSON object per line, each describing a ReplayStep.

    Args:
        name: Case name (without ``.jsonl`` suffix), e.g. ``case_01_single_turn``.

    Returns:
        A fully populated ReplayCase instance.
    """
    path = REPLAY_CASES_DIR / f"{name}.jsonl"
    with open(path, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    if not lines:
        raise ValueError(f"Empty replay case file: {path}")

    meta = json.loads(lines[0])
    steps = [ReplayStep(**json.loads(line)) for line in lines[1:]]
    return ReplayCase(name=meta["name"], description=meta.get("description", ""), steps=steps)


def list_replay_cases() -> List[str]:
    """Return the list of available replay case names (without suffix)."""
    return sorted(p.stem for p in REPLAY_CASES_DIR.glob("case_*.jsonl"))


# ---------------------------------------------------------------------------
# Backend Factory
# ---------------------------------------------------------------------------


async def make_inmemory_service(
    summarizer_manager: Optional[SummarizerSessionManager] = None,
    session_config: Optional[SessionServiceConfig] = None,
) -> InMemorySessionService:
    """Create a configured InMemorySessionService.

    Args:
        summarizer_manager: Optional summarizer manager for summary tests.
        session_config: Optional session config; defaults to no-TTL config.

    Returns:
        Initialized InMemorySessionService instance.
    """
    if session_config is None:
        session_config = _DEFAULT_SESSION_CONFIG.model_copy(deep=True)
    return InMemorySessionService(
        summarizer_manager=summarizer_manager,
        session_config=session_config,
    )


async def make_sqlite_service(
    summarizer_manager: Optional[SummarizerSessionManager] = None,
    session_config: Optional[SessionServiceConfig] = None,
) -> SqlSessionService:
    """Create a configured SqlSessionService backed by an in-memory SQLite database.

    Args:
        summarizer_manager: Optional summarizer manager for summary tests.
        session_config: Optional session config; defaults to no-TTL config.

    Returns:
        Initialized SqlSessionService instance.
    """
    if session_config is None:
        session_config = _DEFAULT_SESSION_CONFIG.model_copy(deep=True)
    # Default to store historical events for persistent backends (aligns with
    # the production SqlSessionService default behaviour).
    session_config.store_historical_events = True
    svc = SqlSessionService(
        db_url="sqlite:///:memory:",
        summarizer_manager=summarizer_manager,
        session_config=session_config,
        is_async=False,
    )
    await svc._sql_storage.create_sql_engine()
    return svc


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------


def normalize_timestamp(ts: float) -> int:
    """Truncate a float timestamp to second precision for comparison."""
    return int(ts)


def normalize_event_for_compare(event: Event) -> Dict[str, Any]:
    """Convert an Event to a normalized dict, stripping runtime-only fields.

    Removes: ``id``, ``invocation_id``, ``timestamp``, ``last_update_time``,
    ``request_id``, ``parent_invocation_id``, ``is_final_response``
    (computed property, differs across backends due to serialization).

    Preserves: ``author``, ``content`` (with all parts), ``partial``,
    ``branch``, ``tag``, ``actions.state_delta``.
    """
    # Start with all fields at their Python values
    state_delta = dict(event.actions.state_delta) if event.actions and event.actions.state_delta else {}
    # Determine event type for easier debugging
    parts_info = []
    if event.content and event.content.parts:
        for p in event.content.parts:
            if p.text:
                parts_info.append({"type": "text", "text": p.text})
            elif p.function_call:
                parts_info.append({
                    "type": "function_call",
                    "name": p.function_call.name,
                    "args": p.function_call.args,
                })
            elif p.function_response:
                parts_info.append({
                    "type": "function_response",
                    "name": p.function_response.name,
                    "response": p.function_response.response,
                })
            elif p.executable_code:
                parts_info.append({"type": "executable_code"})
            elif p.code_execution_result:
                parts_info.append({"type": "code_execution_result"})

    normalized = {
        "author": event.author,
        "partial": event.partial,
        "branch": event.branch,
        "parts": parts_info,
        "state_delta": state_delta,
    }
    return normalized


def normalize_session_for_compare(session: Session) -> Dict[str, Any]:
    """Normalize a Session by retaining only comparable fields.

    Strips: ``events[].id``, all timestamps, ``save_key``.
    Normalizes each event via :func:`normalize_event_for_compare`.
    """
    events_norm = [normalize_event_for_compare(evt) for evt in (session.events or [])]
    return {
        "id": session.id,
        "app_name": session.app_name,
        "user_id": session.user_id,
        "state": dict(session.state or {}),
        "events": events_norm,
        "conversation_count": session.conversation_count,
    }


def normalize_summary_for_compare(summary: Optional[SessionSummary]) -> Optional[Dict[str, Any]]:
    """Normalize a SessionSummary for comparison.

    Strips: ``summary_timestamp`` (backend-dependent).
    Preserves: ``session_id``, ``summary_text``, ``original_event_count``,
    ``compressed_event_count``.
    """
    if summary is None:
        return None
    return {
        "session_id": summary.session_id,
        "summary_text": summary.summary_text.strip() if summary.summary_text else "",
        "original_event_count": summary.original_event_count,
        "compressed_event_count": summary.compressed_event_count,
    }


def normalize_memory_response(response: SearchMemoryResponse) -> List[Dict[str, Any]]:
    """Normalize a memory search response for comparison.

    Strips: ``event.id``, ``event.timestamp``, ``score`` (backend-specific).
    Preserves: ``event.author``, ``event.content.parts[].text`` and
    meaningful fields.
    """
    results = []
    for mem in (response.memories or []):
        if hasattr(mem, "event") and mem.event:
            results.append(normalize_event_for_compare(mem.event))
    return results


# ---------------------------------------------------------------------------
# Pytest Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def inmemory_service():
    """Fixture providing a clean InMemorySessionService."""
    svc = await make_inmemory_service()
    yield svc
    await svc.close()


@pytest.fixture
async def sqlite_service():
    """Fixture providing a clean SqlSessionService backed by in-memory SQLite."""
    svc = await make_sqlite_service()
    yield svc
    await svc.close()


@pytest.fixture
async def backend_pair():
    """Lightweight-mode fixture: yield ``(inmemory, sqlite)`` backend pair.

    Both services are freshly created with default (no-TTL) config.
    """
    inmem = await make_inmemory_service()
    sqlite = await make_sqlite_service()
    yield inmem, sqlite
    await sqlite.close()
    await inmem.close()


# ---------------------------------------------------------------------------
# Mock model for summary tests
# ---------------------------------------------------------------------------


class _MockSummaryResponse:
    """A single mock LLM response chunk for summarization."""

    def __init__(self, text: str):
        self.content = Content(parts=[Part.from_text(text=text)], role="assistant")


class _MockSummaryModel:
    """A mock LLM model that returns a fixed summary text."""
    name = "test-summarizer-mock"

    async def generate_async(self, request: LlmRequest, stream: bool = False, ctx=None):
        """Yield a single mock response with predetermined summary text."""
        yield _MockSummaryResponse("Mock session summary for testing replay consistency.")


def make_mock_summarizer_manager() -> SummarizerSessionManager:
    """Create a SummarizerSessionManager backed by a mock model.

    The mock model returns a fixed summary text, making summary output
    deterministic and comparable across backends.
    """
    model = _MockSummaryModel()
    summarizer = SessionSummarizer(model=model)
    return SummarizerSessionManager(model=model, summarizer=summarizer, auto_summarize=True)


# ---------------------------------------------------------------------------
# Full backend pair with memory
# ---------------------------------------------------------------------------


@pytest.fixture
async def full_backend_pair():
    """Fixture yielding two complete backend tuples for cross-backend comparison.

    Each tuple is ``(session_service, memory_service)``.

    Backend A: InMemorySessionService + InMemoryMemoryService
    Backend B: SqlSessionService + InMemoryMemoryService (same memory impl,
               different session backend)
    """
    # Backend A
    inmem_session = await make_inmemory_service()
    inmem_memory = InMemoryMemoryService(MemoryServiceConfig(enabled=True))

    # Backend B
    sqlite_session = await make_sqlite_service()
    sqlite_memory = InMemoryMemoryService(MemoryServiceConfig(enabled=True))

    yield (inmem_session, inmem_memory), (sqlite_session, sqlite_memory)

    await sqlite_session.close()
    await inmem_session.close()


@pytest.fixture
async def full_backend_pair_with_summary():
    """Like ``full_backend_pair`` but with a mock summarizer attached.

    Each backend gets its own SummarizerSessionManager instance so that
    inject operations targeting only one backend's summary cache work
    correctly.
    """
    mgr_a = make_mock_summarizer_manager()
    mgr_b = make_mock_summarizer_manager()

    inmem_session = await make_inmemory_service(summarizer_manager=mgr_a)
    inmem_memory = InMemoryMemoryService(MemoryServiceConfig(enabled=True))

    sqlite_session = await make_sqlite_service(summarizer_manager=mgr_b)
    sqlite_memory = InMemoryMemoryService(MemoryServiceConfig(enabled=True))

    yield (inmem_session, inmem_memory), (sqlite_session, sqlite_memory)

    await sqlite_session.close()
    await inmem_session.close()
