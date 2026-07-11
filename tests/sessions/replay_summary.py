"""Deterministic summary helpers for replay tests.

The replay harness must not depend on a real LLM. This module provides a tiny
deterministic summarizer that reuses the framework's session compaction logic
while producing stable summary text and metadata.
"""

from __future__ import annotations

import re

from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SummarizerSessionManager


_WHITESPACE_RE = re.compile(r"\s+")


class _ReplaySummaryModel:
    """Minimal model-like object used only for summary metadata."""

    name = "replay-summary-model"


class DeterministicSessionSummarizer(SessionSummarizer):
    """A SessionSummarizer that produces deterministic text without LLM calls."""

    def __init__(self, *, keep_recent_count: int = 2, start_by_user_turn: bool = True) -> None:
        super().__init__(
            model=_ReplaySummaryModel(),  # type: ignore[arg-type]
            keep_recent_count=keep_recent_count,
            start_by_user_turn=start_by_user_turn,
        )
        self._minimum_events = max(keep_recent_count + 1, 2)

    async def should_summarize(self, session: Session) -> bool:
        return len(session.events) >= self._minimum_events

    async def _generate_summary(self, conversation_text: str, ctx=None) -> str:
        normalized = _WHITESPACE_RE.sub(" ", conversation_text).strip()
        if not normalized:
            return ""
        return f"deterministic-summary[{normalized}]"


def build_replay_summarizer_manager(*, keep_recent_count: int) -> SummarizerSessionManager:
    """Create a deterministic summarizer manager for replay tests."""

    summarizer = DeterministicSessionSummarizer(keep_recent_count=keep_recent_count)
    return SummarizerSessionManager(
        model=summarizer.model,
        summarizer=summarizer,
        auto_summarize=True,
    )
