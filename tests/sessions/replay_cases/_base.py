#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Replay case data models and JSON loader for multi-backend consistency testing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class ReplayOp(BaseModel):
    """A single operation in a replay case.

    Supports the following operation types:

    - ``create_session``: initialise a session with ``app_name``, ``user_id``,
      optional ``session_id`` and ``initial_state`` (provided via the
      ``session_setup`` stanza on the enclosing ReplayCase).

    - ``append_event``: construct and append an Event.  Uses ``author``,
      ``text``, optional ``function_call`` / ``function_response`` dicts,
      optional ``state_delta``, and ``partial`` flag.

    - ``update_state``: convenience alias that appends an event carrying a
      ``state_delta``.

    - ``inject_summary``: bypass the LLM summarizer and insert a
      ``SessionSummary`` directly into ``SummarizerSessionManager._summarizer_cache``.

    - ``store_memory``: call ``memory_service.store_session()``.

    - ``search_memory``: call ``memory_service.search_memory()`` with ``query``.

    - ``read_back``: call ``session_service.get_session()`` and capture the
      returned events, state, and summary.

    - ``duplicate_append``: re-append the last-non-read_back event to test
      idempotency / duplicate detection.

    - ``delete_session``: delete the current session.
    """
    op: str
    """Operation identifier."""

    author: str = ""
    """Event author (``"user"`` or agent name)."""

    text: str = ""
    """Plain text content for the event."""

    function_call: Optional[dict] = None
    """Dictionary with ``name`` and ``args`` keys for a FunctionCall."""

    function_response: Optional[dict] = None
    """Dictionary with ``name`` and ``response`` keys for a FunctionResponse."""

    state_delta: dict = Field(default_factory=dict)
    """Key-value pairs to write as ``EventActions.state_delta``."""

    session_id: str = ""
    """Explicit session id (used by ``create_session`` and ``inject_summary``)."""

    summary_text: str = ""
    """Summary body text for ``inject_summary``."""

    original_event_count: int = 0
    """Event count before summarization (``inject_summary``)."""

    compressed_event_count: int = 0
    """Event count after summarization (``inject_summary``)."""

    query: str = ""
    """Search query string for ``search_memory``."""

    partial: bool = False
    """Mark the constructed Event as partial."""

    expected_event_count: Optional[int] = None
    """If set, the read_back result should have this many events."""

    expected_state: Optional[dict] = None
    """If set, the session state after read_back must match this dict."""


class ReplayCase(BaseModel):
    """A complete replay case definition.

    Contains a set of session-creation parameters and a sequenced list of
    operations to execute.
    """
    case_id: str
    """Unique case identifier, e.g. ``"01_single_turn"``."""

    description: str
    """Human-readable description of what this case validates."""

    session_setup: dict = Field(default_factory=dict)
    """Keyword arguments forwarded to ``session_service.create_session()``."""

    operations: list[ReplayOp] = Field(default_factory=list)
    """Ordered list of replay operations."""

    expect_pass: bool = True
    """When ``True`` the case is expected to produce ≤ 5 % diffs.

    Set to ``False`` for injected-anomaly cases that must be detected.
    """

    inject_anomaly: Optional[dict] = None
    """Test-only specification for injecting artificial inconsistency.

    The engine applies this spec to *one* of the two backend results before
    comparison so that the comparator's sensitivity can be verified.

    Example::

        {
            "category": "events",
            "action": "insert_extra",
            "event_index": 2,
            "extra_event": { "author": "hacker", "text": "injected" }
        }
    """


def load_replay_cases(glob_pattern: str = "*.json") -> list[ReplayCase]:
    """Load all replay case JSON files from the ``replay_cases`` directory.

    Returns:
        List of ``ReplayCase`` instances sorted by ``case_id``.
    """
    cases_dir = Path(__file__).parent
    cases: list[ReplayCase] = []

    for filepath in sorted(cases_dir.glob(glob_pattern)):
        if filepath.name.startswith("_"):
            continue
        with open(filepath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        cases.append(ReplayCase(**data))

    return cases
