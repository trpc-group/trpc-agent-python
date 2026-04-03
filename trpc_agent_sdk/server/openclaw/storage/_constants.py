# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Storage constants."""

RECORD_METADATA: str = "metadata"
"""Record metadata."""
RECORD_RAW_EVENT: str = "raw_event"
"""Record raw event."""
RAW_EVENTS_KEY: str = "RAW_EVENTS"
"""Key for old events."""
MEMORY_FILENAME: str = "MEMORY.md"
"""Memory filename."""
HISTORY_FILENAME: str = "HISTORY.md"
"""History filename."""
LONG_TERM_MEMORY_KEY: str = "LONG_TERM_MEMORY"
"""Key for long-term memory."""
HISTORY_KEY: str = "HISTORY"
"""Key for history."""

MAX_FAILURES_BEFORE_RAW_ARCHIVE: int = 3
"""Maximum consecutive LLM failures before falling back to a raw archive."""
MAX_CONSOLIDATION_ROUNDS: int = 5
"""Maximum passes through the consolidation loop for a single create_session_summary call."""
