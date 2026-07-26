# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Replay-aware overrides for the standard session summarizer.

The replay harness annotates each generated summary anchor with deterministic
metadata (summary id, version, supersedes, session id) so cross-backend diffs
can pinpoint summary overwrite / ownership bugs. The SDK's :class:`Event` model
does not expose a generic metadata channel, so the metadata is recorded into a
per-harness side-channel dictionary that the snapshot builder consults while
comparing backend outputs.
"""
from __future__ import annotations

import math
from typing import Any
from typing import Optional

from trpc_agent_sdk.events import Event
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions._session_summarizer import SessionSummarizer

from ._model import ReplaySummaryModel
from ._normalizer import SUMMARY_PREFIX


class ReplaySessionSummarizer(SessionSummarizer):
    """Annotate generated summary anchors with replay ownership metadata."""

    def __init__(self, model: ReplaySummaryModel) -> None:
        super().__init__(
            model=model,
            check_summarizer_functions=[lambda _session: True],
            keep_recent_count=2,
        )
        self._revision: dict[str, Any] = {}
        self.metadata_log: list[dict[str, Any]] = []

    def set_revision(self, summary_id: str, version: int, supersedes: Optional[str]) -> None:
        """Set metadata for the next generated summary anchor."""
        self._revision = {
            "summary_id": summary_id,
            "version": version,
            "supersedes": supersedes,
        }

    def last_revision(self) -> dict[str, Any]:
        """Return the most recently configured revision metadata."""
        return dict(self._revision)

    def drain_metadata(self) -> list[dict[str, Any]]:
        """Return and clear every metadata record emitted so far."""
        emitted = self.metadata_log
        self.metadata_log = []
        return emitted

    async def create_session_summary(
        self,
        session: Session,
        ctx: Optional[object] = None,
        store_historical_events: bool = False,
    ) -> Optional[str]:
        """Generate a summary and record replay metadata before backend persistence."""
        summary_text = await super().create_session_summary(
            session,
            ctx=ctx,
            store_historical_events=store_historical_events,
        )
        if not summary_text:
            return summary_text

        summary_event = next((event for event in session.events if event.is_summary_event()), None)
        if summary_event is None:
            raise RuntimeError("Summarizer returned text without a summary anchor event")

        retained_events = [event for event in session.events if event is not summary_event]
        if retained_events:
            summary_event.timestamp = min(event.timestamp for event in retained_events) - 0.001

        self.metadata_log.append({
            **self._revision,
            "session_id": session.id,
            "summary_text": summary_text,
            "anchor_text": summary_text,
            "anchor_event_id": summary_event.id,
        })
        return summary_text


def is_valid_summary_timestamp(value: object) -> bool:
    """Return True when ``value`` is a finite, positive timestamp."""
    try:
        return bool(value) and math.isfinite(float(value)) and float(value) > 0
    except (TypeError, ValueError):
        return False


def summary_anchor_text(event: Event) -> Optional[str]:
    """Return the normalized text of a summary anchor event."""
    if not event.content or not event.content.parts:
        return None
    text = event.content.parts[0].text
    if text and text.startswith(SUMMARY_PREFIX):
        text = text.removeprefix(SUMMARY_PREFIX)
    from ._normalizer import normalize_summary_text
    return normalize_summary_text(text)