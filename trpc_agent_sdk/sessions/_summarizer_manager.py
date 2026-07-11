# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
#
# Below code are copy and modified from https://github.com/agno-agi/agno.git
#
# Copyright 2025-2026 Agno Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Session summarizer manager for compressing conversation history.

This module provides functionality to summarize conversation history
to reduce memory usage and maintain context in long conversations.
"""

from __future__ import annotations

import time
from typing import Any
from typing import Dict
from typing import Optional

from trpc_agent_sdk.abc import SessionServiceABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel

from ._session import Session
from ._session_summarizer import SessionSummarizer
from ._session_summarizer import SessionSummary


_SUMMARY_EVENT_METADATA_KEY = "session_summary"


class SummarizerSessionManager:
    """Session service with automatic summarization capabilities.

    This service extends the basic session service with automatic
    conversation summarization to reduce memory usage and maintain
    context in long conversations.
    """

    def __init__(
        self,
        model: LLMModel,
        summarizer: Optional[SessionSummarizer] = None,
        auto_summarize: bool = True,
    ):
        """Initialize the summarizer session service.

        Args:
            model: The LLM model to use for summarization
            base_service: The underlying session service to use
            summarizer: The session summarizer to use
            auto_summarize: Whether to automatically summarize sessions
        """
        self._base_service = None
        if not summarizer and model:
            summarizer = SessionSummarizer(model=model)
        self._summarizer: SessionSummarizer = summarizer
        self._auto_summarize = auto_summarize
        self._summarizer_cache: Dict[str, Dict[str, Dict[str, SessionSummary]]] = {}

    def _get_cached_summary(self, session: Session) -> Optional[SessionSummary]:
        """Return cached summary for a session if available."""

        app_name = session.app_name
        user_id = session.user_id
        cached_summary = self._summarizer_cache.get(app_name, {}).get(user_id, {}).get(session.id)
        if cached_summary is not None:
            return cached_summary

        restored_summary = self._restore_summary_from_session(session)
        if restored_summary is not None:
            self._cache_summary(session, restored_summary)
        return restored_summary

    def _cache_summary(self, session: Session, summary: SessionSummary) -> None:
        """Cache a session summary for fast same-process reads."""

        app_name = session.app_name
        user_id = session.user_id
        if app_name not in self._summarizer_cache:
            self._summarizer_cache[app_name] = {}
        if user_id not in self._summarizer_cache[app_name]:
            self._summarizer_cache[app_name][user_id] = {}
        self._summarizer_cache[app_name][user_id][session.id] = summary

    def _get_summary_event(self, session: Session):
        """Return the persisted summary anchor event if present."""

        for event in session.events:
            if event.is_summary_event():
                return event
        return None

    def _serialize_summary(self, summary: SessionSummary) -> Dict[str, Any]:
        """Serialize a summary into event metadata for persistence."""

        return {
            "session_id": summary.session_id,
            "summary_text": summary.summary_text,
            "original_event_count": summary.original_event_count,
            "compressed_event_count": summary.compressed_event_count,
            "summary_timestamp": summary.summary_timestamp,
            "metadata": dict(summary.metadata or {}),
        }

    def _persist_summary_to_session(self, session: Session, summary: SessionSummary) -> None:
        """Attach summary metadata to the persisted summary event."""

        summary_event = self._get_summary_event(session)
        if summary_event is None:
            return
        custom_metadata = dict(summary_event.custom_metadata or {})
        custom_metadata[_SUMMARY_EVENT_METADATA_KEY] = self._serialize_summary(summary)
        summary_event.custom_metadata = custom_metadata
        summary_event.timestamp = summary.summary_timestamp

    def _restore_summary_from_session(self, session: Session) -> Optional[SessionSummary]:
        """Rebuild a SessionSummary from persisted summary-event metadata."""

        summary_event = self._get_summary_event(session)
        if summary_event is None or not summary_event.custom_metadata:
            return None

        persisted_summary = summary_event.custom_metadata.get(_SUMMARY_EVENT_METADATA_KEY)
        if not isinstance(persisted_summary, dict):
            return None

        try:
            return SessionSummary(
                session_id=str(persisted_summary["session_id"]),
                summary_text=str(persisted_summary["summary_text"]),
                original_event_count=int(persisted_summary["original_event_count"]),
                compressed_event_count=int(persisted_summary["compressed_event_count"]),
                summary_timestamp=float(persisted_summary["summary_timestamp"]),
                metadata=dict(persisted_summary.get("metadata") or {}),
            )
        except (KeyError, TypeError, ValueError) as ex:
            logger.warning("Failed to restore persisted summary for session %s: %s", session.id, ex)
            return None

    def _build_summary_metadata(
        self,
        *,
        session: Session,
        original_event_count: int,
        compressed_event_count: int,
        previous_summary: Optional[SessionSummary] = None,
    ) -> Dict[str, Any]:
        """Build stable lineage metadata for a session summary."""
        if previous_summary is None:
            previous_summary = self._get_cached_summary(session)
        previous_metadata = dict(previous_summary.metadata) if previous_summary and previous_summary.metadata else {}
        previous_version = int(previous_metadata.get("version", 0) or 0)
        version = previous_version + 1
        summary_id = f"{session.id}:summary:v{version}"
        summarized_event_count = max(0, original_event_count - compressed_event_count + 1)
        return {
            "summary_id": summary_id,
            "version": version,
            "replaces": previous_metadata.get("summary_id"),
            "summarized_event_count": summarized_event_count,
        }

    def set_session_service(self, session_service: SessionServiceABC, force: bool = False) -> None:
        """Set the session service to use.

        Args:
            session_service: The session service to use
            force: Whether to force update even if already set
        """
        if not self._base_service or force:
            self._base_service = session_service

    def set_summarizer(self, summarizer: SessionSummarizer, force: bool = False) -> None:
        """Set the summarizer to use.

        Args:
            summarizer: The summarizer to use
            force: Whether to force update even if already set
        """
        if not self._summarizer or force:
            self._summarizer = summarizer

    async def create_session_summary(self,
                                     session: Session,
                                     force: bool = False,
                                     ctx: InvocationContext = None) -> None:
        """Create a session summary and compress the session if needed.

        Args:
            session: The session to summarize
        """
        is_should_summarize = await self.should_summarize_session(session) or force
        # Check if session should be summarized
        if is_should_summarize:
            logger.debug("Summarizing session %s", session.id)
            previous_summary = self._get_cached_summary(session)

            # Compress the session so the active events list contains only
            # model-visible summary/recent events. Raw events are retained only
            # when the session service config requests it.
            original_event_count = len(session.events)
            base_config = getattr(self._base_service, "session_config", None)
            store_historical_events = getattr(base_config, "store_historical_events", False)
            if not isinstance(store_historical_events, bool):
                store_historical_events = False
            summary_text = await self._summarizer.create_session_summary(
                session, ctx, store_historical_events=store_historical_events)
            if summary_text:
                summary = SessionSummary(
                    session_id=session.id,
                    summary_text=summary_text,
                    original_event_count=original_event_count,
                    compressed_event_count=len(session.events),
                    summary_timestamp=time.time(),
                    metadata=self._build_summary_metadata(
                        session=session,
                        original_event_count=original_event_count,
                        compressed_event_count=len(session.events),
                        previous_summary=previous_summary,
                    ),
                )
                self._cache_summary(session, summary)
                self._persist_summary_to_session(session, summary)
            # Update the stored session
            if self._base_service:
                await self._base_service.update_session(session)

    async def get_session_summary(self, session: Session) -> Optional[SessionSummary]:
        """Get a summary of a session.

        Args:
            session: The session to summarize

        Returns:
            SessionSummary if successful, None otherwise
        """
        if not self._summarizer:
            return self._restore_summary_from_session(session)
        return self._get_cached_summary(session)

    def get_summarizer_metadata(self) -> Dict[str, Any]:
        """Get metadata about the summarizer configuration.

        Returns:
            Dictionary containing summarizer metadata
        """
        if not self._summarizer:
            return {}

        return self._summarizer.get_summary_metadata()

    async def should_summarize_session(self, session: Session) -> bool:
        """Check if a session should be summarized.

        Args:
            session: The session to check

        Returns:
            True if summarization is needed, False otherwise
        """
        if not self._summarizer or not self._auto_summarize:
            return False

        return await self._summarizer.should_summarize(session)
