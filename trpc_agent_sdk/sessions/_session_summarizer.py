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
from ._utils import find_events_for_summary

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

# Stable keys shared by summary persistence and replay recovery.
# 摘要持久化与回放恢复共同使用的稳定键值。
SESSION_SUMMARY_METADATA_KEY = "session_summary"
SUMMARY_TEXT_PREFIX = "Previous conversation summary:"

# Keep the ordering anchor well above the microsecond storage quantum while
# remaining negligible for TTL semantics and the real timestamp in metadata.
# 排序锚点间隔需明显大于数据库的微秒精度，同时不能影响 TTL；真实更新时间仍保存在元数据中。
_SUMMARY_EVENT_ORDERING_GAP_SECONDS = 0.001


class SessionSummary(BaseModel):
    """Represents a summary of a session's conversation history.

    This class encapsulates the summary information including the summary text,
    metadata about the summarization process, and the versioned replacement
    chain needed to recover the latest persisted summary.

    表示会话历史摘要，包含摘要正文、压缩过程元数据，以及从持久化事件恢复
    最新摘要所需的版本和覆盖链。
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)
    """The pydantic model config. / Pydantic 模型配置。"""
    summary_id: str = ""
    """The persisted summary event ID. / 持久化摘要事件的 ID。"""
    session_id: str
    """The owning session ID. / 摘要所属会话的 ID。"""
    summary_text: str
    """The normalized summary text. / 规范化后的摘要正文。"""
    version: int = 1
    """A session-scoped monotonic version. / 会话内单调递增的摘要版本。"""
    replaces_summary_id: Optional[str] = None
    """The replaced summary event ID, if any. / 被当前版本覆盖的上一摘要事件 ID。"""
    original_event_count: int
    """The event count before compression. / 压缩前的事件数量。"""
    compressed_event_count: int
    """The active event count after compression. / 压缩后的活跃事件数量。"""
    summary_timestamp: float
    """The real summary update time. / 摘要实际生成或更新的时间。"""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    """Additional persisted metadata. / 额外的持久化摘要元数据。"""

    def get_compression_ratio(self) -> float:
        """Get the compression ratio achieved by summarization.

        获取摘要压缩比例；原始事件为空时返回零，避免除零。

        Returns:
            Compression ratio as a percentage (0-100).
            以百分比表示的压缩率（0-100）。
        """
        if self.original_event_count == 0:
            return 0.0
        return (self.original_event_count - self.compressed_event_count) / self.original_event_count * 100

    def to_dict(self) -> Dict[str, Any]:
        """Serialize replay-relevant summary fields to a dictionary.

        将回放所需的摘要 ID、版本、覆盖关系及实际更新时间序列化为字典；
        模型名来自持久化 metadata，而非瞬时模型对象。

        Returns:
            Dictionary representation of the summary.
            摘要的字典表示。
        """
        return {
            "summary_id": self.summary_id,
            "session_id": self.session_id,
            "summary_text": self.summary_text,
            "version": self.version,
            "replaces_summary_id": self.replaces_summary_id,
            "original_event_count": self.original_event_count,
            "compressed_event_count": self.compressed_event_count,
            "summary_timestamp": self.summary_timestamp,
            "model_name": self.metadata.get("model_name"),
            "compression_ratio": self.get_compression_ratio(),
            "metadata": self.metadata,
        }


def session_summary_from_event(event: Event, fallback_session_id: Optional[str] = None) -> Optional[SessionSummary]:
    """Rebuild a versioned summary from a persisted summary event.

    New summary events carry structured metadata. Legacy events are still
    readable by deriving the text, ID, version, and timestamp from the event,
    while session ownership must always be available and valid.

    从持久化摘要事件恢复带版本的摘要。新事件优先读取结构化元数据；旧事件
    则从事件正文、ID、版本和时间戳回退恢复，但必须能确定有效的会话归属。

    Args:
        event: Persisted event that may be a summary anchor.
            可能作为摘要锚点的持久化事件。
        fallback_session_id: Owner used only when legacy metadata has no
            session ID. 仅在旧元数据缺少 session ID 时使用的归属回退值。

    Returns:
        The reconstructed summary, or ``None`` for a non-summary event or
        invalid ownership. 恢复后的摘要；非摘要事件或归属无效时返回 ``None``。
    """
    # Reject ordinary events before interpreting their content as summary data.
    # 先拒绝普通事件，避免把普通文本误解析为摘要数据。
    if not event.is_summary_event():
        return None

    # Structured metadata is authoritative; an empty mapping activates the
    # legacy recovery path without breaking previously stored sessions.
    # 结构化元数据是权威来源；空映射会进入兼容旧存量会话的恢复路径。
    event_metadata = event.custom_metadata or {}
    raw_metadata = event_metadata.get(SESSION_SUMMARY_METADATA_KEY)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else {}

    # Legacy events stored only the prefixed text in Event.content.
    # 旧事件仅在 Event.content 中保存带前缀的摘要正文。
    summary_text = metadata.get("summary_text")
    if not isinstance(summary_text, str):
        summary_text = event.get_text()
        if summary_text.startswith(SUMMARY_TEXT_PREFIX):
            summary_text = summary_text[len(SUMMARY_TEXT_PREFIX):].lstrip()

    # Ownership is strict: the caller's fallback is for legacy compatibility,
    # not permission to override an explicit metadata owner.
    # 会话归属必须严格校验：回退值只兼容旧数据，不能覆盖元数据中的显式归属。
    session_id = metadata.get("session_id", fallback_session_id)
    if not isinstance(session_id, str) or not session_id:
        return None

    # Prefer replay metadata while retaining safe defaults for legacy events.
    # 优先使用回放元数据，同时为旧事件保留安全的字段回退值。
    version = metadata.get("version", event.version or 1)
    original_event_count = metadata.get("original_event_count", 0)
    compressed_event_count = metadata.get("compressed_event_count", 0)
    summary_timestamp = metadata.get("summary_timestamp", event.timestamp)
    replaces_summary_id = metadata.get("replaces_summary_id")

    return SessionSummary(
        summary_id=event.id,
        session_id=session_id,
        summary_text=summary_text,
        version=int(version),
        replaces_summary_id=replaces_summary_id if isinstance(replaces_summary_id, str) else None,
        original_event_count=int(original_event_count),
        compressed_event_count=int(compressed_event_count),
        summary_timestamp=float(summary_timestamp),
        metadata=metadata,
    )


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
            start_by_user_turn: bool = True,  # Whether to start summarization by user turn, default is True
    ):
        """Initialize the session summarizer.

        Args:
            model: The LLM model to use for summarization
            check_summarizer_functions: List of check summarizer functions
            max_summary_length: Maximum length of generated summary
            keep_recent_count: Number of recent events to keep after compression
            start_by_user_turn: Whether to start summarization by user turn, default is True
        """
        self._summarizer_prompt = summarizer_prompt
        self.check_summarizer_functions = check_summarizer_functions or [set_summarizer_conversation_threshold()]
        self.max_summary_length = max_summary_length
        self.__keep_recent_count = keep_recent_count
        self.__start_by_user_turn = start_by_user_turn
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
                                           ctx: InvocationContext | None = None) -> Optional[str]:
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
            if not event.content or not event.content.parts:
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

    async def _generate_summary(self, conversation_text: str, ctx: InvocationContext | None = None) -> str:
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

    async def create_session_summary_by_events(
            self,
            events: List[Event],
            session_id: str,
            keep_recent_count: int = 10,
            ctx: InvocationContext | None = None,
            historical_events: Optional[List[Event]] = None,
            store_historical_events: bool = False) -> tuple[Optional[str], List[Event]]:
        """Compress events and persist a versioned summary anchor.

        The active window becomes ``summary + retained events``. Each new
        summary increments the prior summary version and records its predecessor
        so a backend reload can validate the replacement chain.

        压缩事件并生成可持久化的版本化摘要锚点。活跃窗口会变为“摘要 +
        保留事件”；每次更新递增上一版版本并记录被覆盖摘要，从而让后端重载
        时能够校验覆盖链。

        Args:
            events: The active events to compress. 要压缩的活跃事件。
            session_id: The owning session ID. 摘要所属会话 ID。
            keep_recent_count: Recent events retained after compression.
                压缩后保留的最近事件数。
            ctx: The invocation context. 调用上下文。
            historical_events: Optional destination for replaced raw events.
                可选的被替换原始事件保存列表。
            store_historical_events: Whether to retain replaced raw events.
                是否保留被摘要替换的原始事件。

        Returns:
            A tuple of summary text (or ``None``) and the compressed active
            events. 摘要正文（失败时为 ``None``）和压缩后的活跃事件。
        """
        try:
            original_count = len(events)
            events_for_summary, insert_index = find_events_for_summary(events, keep_recent_count,
                                                                       self.__start_by_user_turn)
            if not events_for_summary:
                return None, events

            # Generate summary of old events
            summary_text = await self._compress_session_to_summary(events_for_summary, session_id, ctx)

            if summary_text:
                # Version is session-local and monotonic; the predecessor ID
                # makes overwrite/replacement errors observable during replay.
                # 版本在会话内单调递增，上一摘要 ID 让覆盖错误在回放中可检测。
                previous_summary_event = next((event for event in reversed(events) if event.is_summary_event()), None)
                summary_version = (previous_summary_event.version or 1) + 1 if previous_summary_event else 1
                summary_timestamp = time.time()
                compressed_count = 1 + len(events[insert_index:])
                retained_events = events[insert_index:]
                # SQL reconstructs the event list by timestamp. The summary is
                # an anchor that must precede retained events even though it was
                # generated later, so use an ordering timestamp just before the
                # first retained event and keep the actual update time in
                # structured summary metadata.
                # SQL 会按时间戳重建事件顺序。摘要虽然后生成，却必须位于保留
                # 事件之前，因此事件时间用于排序；实际更新时间另存于结构化元数据。
                # A one-microsecond float offset can round to the same SQL
                # datetime value, so use a millisecond-scale ordering gap.
                # 浮点数减一微秒在转换为 SQL datetime 时可能舍入成相同值，
                # 因此使用毫秒级排序间隔。
                summary_event_timestamp = (
                    min(event.timestamp for event in retained_events) - _SUMMARY_EVENT_ORDERING_GAP_SECONDS
                    if retained_events else summary_timestamp)
                # Persist the summary as a normal event so every session backend
                # can reload it without a backend-specific summary table.
                # 将摘要作为普通事件持久化，使所有后端无需专用摘要表即可重载。
                summary_event = Event(invocation_id="summary",
                                      author="system",
                                      content=Content(
                                          parts=[Part.from_text(text=f"{SUMMARY_TEXT_PREFIX} {summary_text}")],
                                          role="user"),
                                      timestamp=summary_event_timestamp,
                                      version=summary_version)
                summary_event.set_summary_event(True)
                # Store semantic text and strict replay metadata separately:
                # owner, version, and replacement chain must never be normalized away.
                # 正文与严格回放元数据分开保存：归属、版本及覆盖链不得被归一化忽略。
                summary_event.custom_metadata = {
                    SESSION_SUMMARY_METADATA_KEY: {
                        "summary_id": summary_event.id,
                        "session_id": session_id,
                        "summary_text": summary_text,
                        "version": summary_version,
                        "replaces_summary_id": previous_summary_event.id if previous_summary_event else None,
                        "original_event_count": original_count,
                        "compressed_event_count": compressed_count,
                        "summary_timestamp": summary_timestamp,
                        "model_name": self.model.name if self.model else None,
                    }
                }

                summarized_events = events[:insert_index]
                if store_historical_events and historical_events is not None:
                    # Historical storage preserves raw context replaced by the anchor.
                    # historical_events 保存被摘要锚点替换的原始上下文。
                    historical_events.extend(summarized_events)

                # Keep only the summary and recent active events in the model-facing window.
                # 模型可见窗口只保留摘要锚点及最近事件。
                events[:] = [summary_event] + events[insert_index:]

                logger.info("Compressed session %s: %s events -> %s events", session_id, original_count,
                            compressed_count)

            return summary_text, events
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to compress session %s: %s", session_id, ex, exc_info=True)
            return None, events

    async def create_session_summary(self,
                                     session: Session,
                                     ctx: InvocationContext | None = None,
                                     store_historical_events: bool = False) -> Optional[str]:
        """Compress a session by summarizing old events.

        Args:
            session: The session to compress
            ctx: The invocation context
            store_historical_events: Whether to keep raw historical events

        Returns:
            Summary text if successful, None otherwise
            Events after compression
        """
        summary_text, _ = await self.create_session_summary_by_events(session.events,
                                                                      session.id,
                                                                      self.__keep_recent_count,
                                                                      ctx,
                                                                      historical_events=session.historical_events,
                                                                      store_historical_events=store_historical_events)
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
