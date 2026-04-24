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
"""Session summarizer for compressing conversation history.

This module provides functionality to summarize conversation history
to reduce memory usage and maintain context in long conversations.
"""

from __future__ import annotations

import json
import time
from textwrap import dedent
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._session import Session
from ._summarizer_checker import CheckSummarizerFunction
from ._summarizer_checker import set_summarizer_conversation_threshold

DEFAULT_SUMMARIZER_PROMPT = dedent("""\
Please summarize the following conversation, focusing on:
1. Key decisions made
2. Important information shared
3. Actions taken or planned
4. Context that should be remembered for future interactions

Keep the summary concise but comprehensive. Focus on what would be most important to remember
for continuing the conversation.

Conversation:
{conversation_text}

Summary:""")


class SessionSummary(BaseModel):
    """Represents a summary of a session's conversation history.

    This class encapsulates the summary information including the summary text,
    metadata about the summarization process, and references to the original events.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    """The pydantic model config."""
    session_id: str
    """The ID of the session being summarized."""
    summary_text: str
    """The summary text."""
    original_event_count: int
    """The number of events before summarization."""
    compressed_event_count: int
    """The number of events after summarization."""
    summary_timestamp: float
    """The timestamp when the summary was created."""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    """Additional metadata about the summarization."""

    def get_compression_ratio(self) -> float:
        """Get the compression ratio achieved by summarization.

        Returns:
            Compression ratio as a percentage (0-100)
        """
        if self.original_event_count == 0:
            return 0.0
        return (self.original_event_count - self.compressed_event_count) / self.original_event_count * 100

    def to_dict(self) -> Dict[str, Any]:
        """Convert summary to dictionary representation.

        Returns:
            Dictionary representation of the summary
        """
        return {
            "session_id": self.session_id,
            "summary_text": self.summary_text,
            "original_event_count": self.original_event_count,
            "compressed_event_count": self.compressed_event_count,
            "summary_timestamp": self.summary_timestamp,
            "model_name": self.model.name,
            "compression_ratio": self.get_compression_ratio(),
            "metadata": self.metadata,
        }


class SessionSummarizer:
    """Summarizes conversation history to reduce memory usage.

    This class provides functionality to compress long conversation histories
    into concise summaries while preserving important context and decisions.
    """

    def __init__(
        self,
        model: LLMModel,
        summarizer_prompt: str = DEFAULT_SUMMARIZER_PROMPT,
        check_summarizer_functions: Optional[List[CheckSummarizerFunction]] = None,
        max_summary_length: int = 1000,
        keep_recent_count: int = 10,
    ):
        """Initialize the session summarizer.

        Args:
            model: The LLM model to use for summarization
            check_summarizer_functions: List of check summarizer functions
            max_summary_length: Maximum length of generated summary
            keep_recent_count: Number of recent events to keep after compression
        """
        self._summarizer_prompt = summarizer_prompt
        self.check_summarizer_functions = check_summarizer_functions or [set_summarizer_conversation_threshold()]
        self.max_summary_length = max_summary_length
        self.__keep_recent_count = keep_recent_count

        # Initialize LLM model for summarization
        self._model = model

    @property
    def model(self) -> LLMModel:
        """Get the LLM model for summarization."""
        return self._model

    async def should_summarize(self, session: Session) -> bool:
        """Check if the session should be summarized.

        Args:
            session: The session to check

        Returns:
            True if summarization is needed, False otherwise
        """

        if not session.events:
            return False

        for check_summarizer_function in self.check_summarizer_functions:
            if not check_summarizer_function(session):
                return False

        return True

    def _has_important_content(self, events: List[Event]) -> bool:
        """Check if events contain important content worth summarizing.

        Args:
            events: List of events to check

        Returns:
            True if events contain important content, False otherwise
        """
        if not events:
            return False

        # Check for events with meaningful content
        for event in events:
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if part.text and len(part.text.strip()) > 10:
                        return True
        return False

    async def _compress_session_to_summary(self,
                                           events: List[Event],
                                           session_id: str,
                                           ctx: InvocationContext = None) -> Optional[str]:
        """Generate a summary for a session.

        Args:
            events: The events to summarize
            session_id: The session ID
            keep_recent_count: Number of recent events to keep after compression
            ctx: The invocation context

        Returns:
            Summary text if successful, None otherwise
        """
        try:
            if not events or not self._model:
                logger.debug("No events to summarize for session %s", session_id)
                return None

            # Extract conversation text from events
            conversation_text = self._extract_conversation_text(events)
            if not conversation_text:
                logger.debug("No conversation text extracted for session %s", session_id)
                return None

            # Generate summary using LLM
            summary = await self._generate_summary(conversation_text, ctx)
            if summary:
                logger.info("Generated summary for session %s: %s characters", session_id, len(summary))
                return summary
            else:
                logger.warning("Failed to generate summary for session %s", session_id)
                return None

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error summarizing session %s: %s", session_id, ex, exc_info=True)
            return None

    def _extract_conversation_text(self, events: List[Event]) -> str:
        """Extract conversation text from events.

        Args:
            events: List of events to extract text from

        Returns:
            Concatenated conversation text
        """
        conversation_parts = []
        # To ensure compatibility with streaming events,
        # it is necessary to merge events that share the same author and branch.
        current_author = None
        current_branch = None
        current_text = ""

        for event in events:
            if not event.is_model_visible():
                continue
            if not event.content or not event.content.parts:
                continue

            # Skip events that should not be included in summary
            if event.actions and event.actions.skip_summarization:
                continue

            # Extract text、tool_call、tool_response from event parts
            event_text = ""
            for part in event.content.parts:
                if part.text:
                    event_text += part.text

                if part.function_call:
                    tool_args_str = json.dumps(part.function_call.args, ensure_ascii=False) if isinstance(
                        part.function_call.args, dict) else str(part.function_call.args)
                    event_text += f"\n<tool_call><tool_name>{part.function_call.name}</tool_name>"
                    event_text += f"<tool_args>{tool_args_str}</tool_args></tool_call>\n"
                if part.function_response:
                    func_response = part.function_response
                    event_text += f"\n<tool_response><tool_name>{func_response.name}</tool_name>"
                    tool_response_str = json.dumps(func_response.response, ensure_ascii=False) if isinstance(
                        func_response.response, dict) else str(func_response.response)
                    event_text += tool_response_str
                    event_text += "</tool_response>\n"

            if not event_text.strip():
                continue

            author = event.author if event.author else "unknown"
            branch = event.branch if event.branch else "unknown"
            is_partial = event.partial

            # Check if we should merge with previous event
            # Merge condition: current event is partial AND has same author as accumulated
            if not is_partial:
                # Flush previous accumulated text if any
                if current_text.strip():
                    conversation_parts.append(f"{current_author}: {current_text.strip()}")
                # Not partial, add the event text to the conversation parts
                conversation_parts.append(f"{author}: {event_text.strip()}")
                # start new empty accumulated
                current_author = author
                current_branch = branch
                current_text = ""
            if is_partial and current_author == author and current_text and current_branch == branch:
                # Merge with current accumulated text
                current_text += event_text
            else:
                # Flush previous accumulated text if any
                if current_text.strip():
                    conversation_parts.append(f"{current_author}: {current_text.strip()}")
                # Start new accumulation
                current_author = author
                current_branch = branch
                current_text = event_text

        # Don't forget to flush the last accumulated text
        if current_text.strip():
            conversation_parts.append(f"{current_author}: {current_text.strip()}")

        return "\n".join(conversation_parts)

    async def _generate_summary(self, conversation_text: str, ctx: InvocationContext = None) -> str:
        """Generate a summary using the LLM model.

        Args:
            conversation_text: The conversation text to summarize

        Returns:
            Generated summary text
        """
        try:
            # Create summarization prompt
            prompt = self._create_summarization_prompt(conversation_text)

            # Create LLM request
            request = LlmRequest()
            request.contents.append(Content(role="user", parts=[Part.from_text(text=prompt)]))

            # Extract summary from response
            summary = ""
            async for llm_response in self._model.generate_async(request, stream=False, ctx=ctx):
                if llm_response.content and llm_response.content.parts:
                    for part in llm_response.content.parts:
                        if part.text:
                            summary += part.text

            # Truncate if too long
            if len(summary) > self.max_summary_length:
                summary = summary[:self.max_summary_length] + "..."

            return summary.strip()

        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Error generating summary: %s", ex, exc_info=True)
            return ""

    def _create_summarization_prompt(self, conversation_text: str) -> str:
        """Create a prompt for summarization.

        Args:
            conversation_text: The conversation text to summarize

        Returns:
            Formatted prompt for the LLM
        """
        return self._summarizer_prompt.format(conversation_text=conversation_text)

    async def create_session_summary_by_events(self,
                                               events: List[Event],
                                               session_id: str,
                                               keep_recent_count: int | None = None,
                                               ctx: InvocationContext = None) -> Optional[str]:
        """Compress a session by summarizing old events.

        Args:
            events: The events to compress
            session_id: The session ID
            keep_recent_count: Number of recent events to keep after compression
            ctx: The invocation context

        Returns:
            Summary text if successful, None otherwise
            Events after compression
        """
        if keep_recent_count is None:
            old_events = events
        else:
            old_events = events[:-keep_recent_count]
        try:
            original_count = sum(1 for event in events if event.is_model_visible())
            old_visible_events = [event for event in old_events if event.is_model_visible()]
            if not old_visible_events:
                return None, events

            # Generate summary of old events
            summary_text = await self._compress_session_to_summary(old_visible_events, session_id, ctx)

            if summary_text:
                # Create summary event
                summary_event = Event(invocation_id="summary",
                                      author="system",
                                      content=Content(
                                          parts=[Part.from_text(text=f"Previous conversation summary: {summary_text}")],
                                          role="system"),
                                      timestamp=time.time())
                summary_event.set_summary_event(True)
                summary_event.set_model_visible(True)

                # Hide old visible events from model history without dropping raw data.
                for event in old_visible_events:
                    event.set_model_visible(False)

                # Insert summary near the old/recent boundary while preserving all events.
                insert_index = len(old_events)
                events.insert(insert_index, summary_event)

                compressed_count = sum(1 for event in events if event.is_model_visible())
                logger.info("Compressed session %s: %s events -> %s events", session_id, original_count,
                            compressed_count)

            return summary_text, events
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to compress session %s: %s", session_id, ex, exc_info=True)
            return None, events

    async def create_session_summary(self, session: Session, ctx: InvocationContext = None) -> Optional[str]:
        """Compress a session by summarizing old events.

        Args:
            session: The session to compress
            ctx: The invocation context

        Returns:
            Summary text if successful, None otherwise
            Events after compression
        """
        summary_text, _ = await self.create_session_summary_by_events(session.events, session.id,
                                                                      self.__keep_recent_count, ctx)
        return summary_text

    def get_summary_metadata(self) -> Dict[str, Any]:
        """Get metadata about the summarizer configuration.

        Returns:
            Dictionary containing summarizer metadata
        """
        return {
            "model_name": self.model.name,
            "max_summary_length": self.max_summary_length,
            "keep_recent_count": self.__keep_recent_count,
            "model_available": self._model is not None,
        }
