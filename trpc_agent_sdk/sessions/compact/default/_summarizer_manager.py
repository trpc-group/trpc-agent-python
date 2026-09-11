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
from typing_extensions import override

from trpc_agent_sdk.abc import CompactSummarizerManagerABC
from trpc_agent_sdk.abc import CompactTrigger
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel

from ..._session import Session
from ._summarizer import DefaultSessionSummarizer
from ._summarizer import DefaultSessionSummary


class DefaultSessionSummarizerManager(CompactSummarizerManagerABC):
    """Session service with automatic summarization capabilities.

    This service extends the basic session service with automatic
    conversation summarization to reduce memory usage and maintain
    context in long conversations.
    """

    def __init__(
        self,
        model: LLMModel,
        summarizer: Optional[DefaultSessionSummarizer] = None,
        auto_summarize: bool = True,
        compact_trigger: CompactTrigger = CompactTrigger.AFTER_TURN,
    ):
        """Initialize the summarizer session service.

        Args:
            model: The LLM model to use for summarization
            base_service: The underlying session service to use
            summarizer: The session summarizer to use
            auto_summarize: Whether to automatically summarize sessions
        """
        if not summarizer and model:
            summarizer = DefaultSessionSummarizer(model=model)
        super().__init__(summarizer, compact_trigger=compact_trigger)
        self._auto_summarize = auto_summarize
        self._summarizer_cache: Dict[str, Dict[str, Dict[str, DefaultSessionSummary]]] = {}

    @override
    async def create_session_summary(self,
                                     session: Session,
                                     force: bool = False,
                                     ctx: InvocationContext = None) -> None:
        """Create a session summary and compress the session if needed.

        Args:
            session: The session to summarize
        """
        if self.compact_trigger != CompactTrigger.AFTER_TURN:
            return
        is_should_summarize = await self.should_summarize_session(session) or force
        # Check if session should be summarized
        if is_should_summarize:
            logger.debug("Summarizing session %s", session.id)

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
                app_name = session.app_name
                user_id = session.user_id
                if app_name not in self._summarizer_cache:
                    self._summarizer_cache[app_name] = {}
                if user_id not in self._summarizer_cache[app_name]:
                    self._summarizer_cache[app_name][user_id] = {}
                self._summarizer_cache[app_name][user_id][session.id] = DefaultSessionSummary(
                    session_id=session.id,
                    summary_text=summary_text,
                    original_event_count=original_event_count,
                    compressed_event_count=len(session.events),
                    summary_timestamp=time.time(),
                    model_name=getattr(self._summarizer.model, "name", ""),
                )
                session.conversation_count = 0
            # Update the stored session
            if self._base_service:
                await self._base_service.update_session(session)

    @override
    async def get_session_summary(self, session: Session) -> Optional[DefaultSessionSummary]:
        """Get a summary of a session.

        Args:
            session: The session to summarize

        Returns:
            DefaultSessionSummary if successful, None otherwise
        """
        if not self._summarizer or not self._summarizer_cache:
            return None
        app_name = session.app_name
        user_id = session.user_id

        if app_name not in self._summarizer_cache or user_id not in self._summarizer_cache[
                app_name] or session.id not in self._summarizer_cache[app_name][user_id]:
            return None

        return self._summarizer_cache[app_name][user_id][session.id]

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
