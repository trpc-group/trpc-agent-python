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
from ._session_summarizer import session_summary_from_event


class SummarizerSessionManager:
    """Session service with automatic summarization capabilities.

    This service extends the basic session service with automatic
    conversation summarization to reduce memory usage and maintain
    context in long conversations. Persisted summary events are the recovery
    source of truth; the in-process cache is only an acceleration layer.

    为会话提供自动摘要能力。持久化摘要事件是恢复时的事实来源，进程内缓存
    仅用于加速读取。
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
        """Create, cache, and persist a summary when compression is needed.

        The cache is rebuilt from the newly persisted summary event whenever
        possible so version, replacement chain, owner, and real update time
        exactly match backend data.

        在需要压缩时创建、缓存并持久化摘要。优先从新生成的持久化摘要事件
        建立缓存，确保版本、覆盖链、归属和实际更新时间与后端数据完全一致。

        Args:
            session: The session to summarize. 要生成摘要的会话。
            force: Bypass the configured summary threshold. 是否跳过阈值强制摘要。
            ctx: Optional invocation context. 可选调用上下文。
        """
        is_should_summarize = await self.should_summarize_session(session) or force
        # Respect the configured threshold unless the caller explicitly forces compression.
        # 除非调用方显式强制，否则遵循已配置的摘要阈值。
        if is_should_summarize:
            logger.debug("Summarizing session %s", session.id)

            # Compress the session so the active events list contains only
            # model-visible summary/recent events. Raw events are retained only
            # when the session service config requests it.
            # 压缩后活跃窗口只包含模型可见的摘要和最近事件；仅在后端配置要求时
            # 才把被替换的原始事件保存到 historical_events。
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
                # Rehydrate from the event rather than duplicating metadata
                # construction, keeping cache and persistent replay semantics aligned.
                # 从事件反序列化而非重复拼装元数据，保证缓存与持久化回放语义一致。
                summary_event = next((event for event in reversed(session.events) if event.is_summary_event()), None)
                persisted_summary = (session_summary_from_event(summary_event, session.id)
                                     if summary_event is not None else None)
                # The fallback supports custom/legacy summarizers that return
                # text without emitting a structured summary event.
                # 回退对象兼容只返回文本、未生成结构化摘要事件的自定义或旧摘要器。
                self._summarizer_cache[app_name][user_id][session.id] = persisted_summary or SessionSummary(
                    session_id=session.id,
                    summary_text=summary_text,
                    original_event_count=original_event_count,
                    compressed_event_count=len(session.events),
                    summary_timestamp=time.time(),
                )
            # Persist the compressed window and summary event as one session snapshot.
            # 将压缩后的窗口和摘要事件作为同一份会话快照持久化。
            if self._base_service:
                await self._base_service.update_session(session)

    async def get_session_summary(self, session: Session) -> Optional[SessionSummary]:
        """Get a valid summary from cache or recover it from persisted events.

        Cache hits and recovered summaries must belong to the requested session.
        On cache miss, the highest ``(version, timestamp)`` summary event is
        selected and cached, making process restarts transparent.

        从缓存获取有效摘要，或从持久化事件恢复。缓存值与恢复值都必须归属于
        当前会话；缓存未命中时选择 ``(version, timestamp)`` 最大的摘要事件并
        回填缓存，使进程重启不导致摘要丢失。

        Args:
            session: The session whose summary is requested. 要读取摘要的会话。

        Returns:
            A valid session-owned summary, or ``None``.
            归属于当前会话的有效摘要；不存在或校验失败时返回 ``None``。
        """
        if not self._summarizer:
            return None
        app_name = session.app_name
        user_id = session.user_id

        cached = self._summarizer_cache.get(app_name, {}).get(user_id, {}).get(session.id)
        if cached is not None:
            # Never trust a cache key alone; verify ownership stored in the value.
            # 不能只信任缓存键，还必须校验缓存值中记录的会话归属。
            if cached.session_id != session.id:
                logger.warning(
                    "Ignoring cached summary %s with invalid session ownership for %s",
                    cached.summary_id,
                    session.id,
                )
                return None
            return cached

        # Recover after cache loss/restart from summary anchors stored with the session.
        # 缓存丢失或进程重启后，从随会话持久化的摘要锚点恢复。
        summary_events = [event for event in session.events if event.is_summary_event()]
        if not summary_events:
            return None
        # Version defines replacement order; timestamp breaks ties for legacy or
        # malformed data that reused a version.
        # 版本决定覆盖先后；时间戳为旧数据或错误复用版本的情况提供稳定决胜规则。
        summary_event = max(summary_events, key=lambda event: (event.version, event.timestamp))
        summary = session_summary_from_event(summary_event, session.id)
        # Explicit metadata ownership must match even when a legacy fallback was supplied.
        # 即使提供了旧数据回退值，元数据中的显式归属仍必须与当前会话一致。
        if summary is None or summary.session_id != session.id:
            logger.warning("Ignoring summary event %s with invalid session ownership for %s", summary_event.id,
                           session.id)
            return None

        # Repopulate the acceleration cache with the fully reconstructed metadata.
        # 使用完整恢复的元数据回填加速缓存。
        self._summarizer_cache.setdefault(app_name, {}).setdefault(user_id, {})[session.id] = summary
        return summary

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
