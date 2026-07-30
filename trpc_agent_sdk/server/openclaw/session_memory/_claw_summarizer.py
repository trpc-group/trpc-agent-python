# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""trpc_claw-style dual-layer memory summarizer for trpc_claw session.

This module is used to summarize the session history and memory.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from textwrap import dedent
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.context import get_invocation_ctx
from trpc_agent_sdk.context import set_invocation_ctx
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions import CheckSummarizerFunction
from trpc_agent_sdk.sessions import Session
from trpc_agent_sdk.sessions import SessionSummarizer
from trpc_agent_sdk.sessions import SessionSummary
from trpc_agent_sdk.sessions import SummarizerSessionManager
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ..storage import HISTORY_KEY
from ..storage import LONG_TERM_MEMORY_KEY
from ..storage import MAX_CONSOLIDATION_ROUNDS
from ..storage import MAX_FAILURES_BEFORE_RAW_ARCHIVE
from ..storage import RAW_EVENTS_KEY
from ..storage import StorageManager
from ..storage import get_memory_key

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

CLAW_SUMMARIZER_PROMPT = dedent("""\
You are a memory consolidation agent.  Process the conversation below and
consolidate it into persistent memory.

## Current Long-term Memory
{current_memory}

## Conversation to Process
{conversation_text}

Respond with **exactly** the following XML structure (include the tags):

<history_entry>
[{timestamp}] A concise paragraph summarizing the key events, decisions, and
topics covered in this conversation.  Include enough detail for later grep/search.
</history_entry>

<memory_update>
Full updated long-term memory written as Markdown.  Incorporate ALL existing
facts from "Current Long-term Memory" above, plus any important new facts
learned in this conversation.  If nothing new was learned, reproduce the
current memory unchanged.
</memory_update>
""")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_llm_response(text: str) -> tuple[str, str]:
    """Parse ``<history_entry>`` and ``<memory_update>`` from an LLM response.

    Falls back gracefully: if either tag is missing the whole text is used as
    ``memory_update`` and a truncated excerpt becomes ``history_entry``.

    Args:
        text: The text to parse.

    Returns:
        tuple[str, str]: The history entry and memory update.
        - history_entry: The history entry.
        - memory_update: The memory update.
    """
    history_entry = ""
    memory_update = ""

    history_match = re.search(r"<history_entry>(.*?)</history_entry>", text, re.DOTALL | re.IGNORECASE)
    memory_match = re.search(r"<memory_update>(.*?)</memory_update>", text, re.DOTALL | re.IGNORECASE)

    if history_match:
        history_entry = history_match.group(1).strip()
    if memory_match:
        memory_update = memory_match.group(1).strip()

    # Graceful fallback when tags are absent
    if not memory_update and text.strip():
        memory_update = text.strip()
        history_entry = history_entry or text[:300].strip()

    return history_entry, memory_update


# ---------------------------------------------------------------------------
# ClawSessionSummarizer
# ---------------------------------------------------------------------------


class ClawSessionSummarizer(SessionSummarizer):
    """trpc_claw-style summarizer producing *history_entry* + *memory_update*.

    The prompt asks for two structured outputs: a searchable history entry
    and a full long-term memory update.
    ``session.summary_events`` is updated with a **single** event containing
    the latest ``memory_update`` (long-term memory).
    ``session.events`` is trimmed to the most recent *keep_recent_count*
    events; older events are preserved only in ``session.raw_events``.
    """

    def __init__(
        self,
        model: LLMModel,
        storage_manager: StorageManager,
        summarizer_prompt: str = CLAW_SUMMARIZER_PROMPT,
        max_summary_length: int = 4000,
        keep_recent_count: int = 10,
        check_summarizer_functions: Optional[List[CheckSummarizerFunction]] = None,
    ) -> None:
        """Initialize the claw session summarizer.

        Args:
            model: The model to use for summarization.
            storage_manager: The storage manager to use for memory.
            summarizer_prompt: The summarizer prompt.
            max_summary_length: The maximum summary length.
            keep_recent_count: The number of recent events to keep.
            check_summarizer_functions: The check summarizer functions.
        """
        super().__init__(
            model=model,
            summarizer_prompt=summarizer_prompt,
            check_summarizer_functions=check_summarizer_functions,
            max_summary_length=max_summary_length,
            keep_recent_count=keep_recent_count,
        )
        self._storage_manager: StorageManager = storage_manager
        # Store separately so subclass can access without name-mangling dance.
        self._claw_keep_recent = keep_recent_count
        self._consecutive_failures: int = 0

    @property
    def storage_manager(self) -> StorageManager:
        """Get the storage manager."""
        return self._storage_manager

    @override
    async def create_session_summary(self, session: Session, ctx: InvocationContext = None) -> Optional[str]:
        """Compress *session* with trpc_claw-style dual-layer memory.

        Args:
            session: The session to summarize.
            ctx: The invocation context.

        Returns:
            Optional[str]: The history entry, or None if nothing was produced.
        """
        if not ctx:
            raise ValueError("Invocation context is required")
        keep_n = self._claw_keep_recent
        all_events = session.events

        if keep_n and len(all_events) <= keep_n:
            logger.debug(
                "Session %s has too few events to summarize (%d <= %d)",
                session.id,
                len(all_events),
                keep_n,
            )
            return None

        old_events, recent_events = self._find_safe_split(all_events, keep_n)
        if not old_events:
            return None

        memory_key = get_memory_key(session)
        current_memory = await self._storage_manager.read_long_term(memory_key)

        conversation_text = self._extract_conversation_text(old_events)
        if not conversation_text.strip():
            logger.debug("No extractable text in old events for session %s", session.id)
            return None

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        prompt = self._summarizer_prompt.format(
            current_memory=current_memory,
            conversation_text=conversation_text,
            timestamp=timestamp,
        )
        history_entry, memory_update = await self._call_llm_for_memory(prompt, session.id, ctx)

        if not memory_update:
            self._consecutive_failures += 1
            if self._consecutive_failures >= MAX_FAILURES_BEFORE_RAW_ARCHIVE:
                logger.warning(
                    "Session %s: %d consecutive LLM failures — falling back to raw archive",
                    session.id,
                    self._consecutive_failures,
                )
                history_entry, memory_update = self._make_raw_archive(old_events, current_memory)
                self._consecutive_failures = 0
            else:
                logger.warning(
                    "Session %s: LLM consolidation failed (failure %d/%d), will retry next time",
                    session.id,
                    self._consecutive_failures,
                    MAX_FAILURES_BEFORE_RAW_ARCHIVE,
                )
                return None  # Don't advance; try again next time.
        else:
            self._consecutive_failures = 0  # Reset on success.

        # Enforce max length.
        if len(memory_update) > self.max_summary_length:
            memory_update = memory_update[:self.max_summary_length] + "…"

        memory_event = Event(
            invocation_id="memory_consolidation",
            author="system",
            content=Content(
                parts=[Part.from_text(text=memory_update)],
                role="system",
            ),
            timestamp=time.time(),
        )

        ctx.agent_context.with_metadata(LONG_TERM_MEMORY_KEY, memory_event)
        raw_events_event: list[Event] = ctx.agent_context.get_metadata(RAW_EVENTS_KEY, [])
        raw_events_event.extend(old_events)
        if old_events:
            ctx.agent_context.with_metadata(RAW_EVENTS_KEY, raw_events_event)
        session.events = recent_events

        logger.info(
            "Session %s consolidated: %d old events → %d-char memory; %d recent kept",
            session.id,
            len(old_events),
            len(memory_update),
            len(recent_events),
        )
        return history_entry

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _invoke_llm(self, prompt: str, ctx: InvocationContext) -> str:
        """Single raw LLM invocation — returns the concatenated text response.

        Args:
            prompt: The prompt to invoke the LLM with.
            ctx: The invocation context.

        Returns:
            str: The concatenated text response.
        """
        request = LlmRequest()
        request.contents.append(Content(role="user", parts=[Part.from_text(text=prompt)]))
        raw_text = ""
        async for llm_response in self._model.generate_async(request, stream=False, ctx=ctx):
            if llm_response.content and llm_response.content.parts:
                for part in llm_response.content.parts:
                    if part.text:
                        raw_text += part.text
        return raw_text

    async def _call_llm_for_memory(
        self,
        prompt: str,
        session_id: str,
        ctx: InvocationContext = None,
    ) -> tuple[str, str]:
        """Call the LLM with up to 2 attempts and validate the parsed response.

        If the first response is empty or fails XML validation a second attempt is made before giving up.
        ``memory_update`` must be at least 20 characters long (guards against the LLM echoing only the XML tags
            or returning a blank body).
        A response that passes regex parsing but is too short is treated the same as a parse failure and
        triggers a retry.

        Returns ``("", "")`` when all attempts fail (the caller's failure counter will decide whether to raw-archive or
        simply skip this round).

        Args:
            prompt: The prompt to invoke the LLM with.
            session_id: The session ID.
            ctx: The invocation context.

        Returns:
            tuple[str, str]: The history entry and memory update.
            - history_entry: The history entry.
            - memory_update: The memory update.
        """
        for attempt in range(1, 3):  # attempts 1 and 2
            try:
                raw_text = await self._invoke_llm(prompt, ctx)
                if not raw_text.strip():
                    logger.warning(
                        "Session %s attempt %d: LLM returned empty response",
                        session_id,
                        attempt,
                    )
                    continue

                history_entry, memory_update = _parse_llm_response(raw_text)

                # Validate — guard against truncated or tag-only responses.
                if not memory_update or len(memory_update.strip()) < 20:
                    logger.warning(
                        "Session %s attempt %d: memory_update too short (%d chars) — retrying",
                        session_id,
                        attempt,
                        len(memory_update),
                    )
                    continue

                return history_entry, memory_update

            except Exception as exc:  # pylint: disable=broad-except
                logger.error(
                    "Session %s attempt %d: LLM error during memory consolidation: %s",
                    session_id,
                    attempt,
                    exc,
                    exc_info=True,
                )

        return "", ""

    def _find_safe_split(self, events: List[Event], keep_n: int) -> tuple[List[Event], List[Event]]:
        """Split *events* at a user-turn boundary to avoid orphaned tool pairs.

        The naive ``events[:-keep_n]`` cut can slice in the middle of a
        ``tool_call -> tool_response`` pair, leaving the LLM with an orphaned
        tool response in the recent window and its matching call in the
        to-be-summarized bucket.

        Starting from ``len(events) - keep_n`` (the ideal split), scan backward until an event authored by ``"user"``
        is found.  Split immediately before that event so the entire user turn (plus any following model / tool events)
        stays in the recent bucket intact.

        If no user event is found before the ideal split (e.g. the session starts with a long system preamble)
        the ideal split is used as-is.

        Args:
            events: The events to split.
            keep_n: The number of recent events to keep.

        Returns:
            tuple[List[Event], List[Event]]: The old events and recent events.
            - old_events: The old events.
            - recent_events: The recent events.
        """
        if not keep_n or len(events) <= keep_n:
            return [], list(events)

        ideal = len(events) - keep_n

        for i in range(ideal, 0, -1):
            if events[i].author and events[i].author.lower() == "user":
                if i > 0:
                    return list(events[:i]), list(events[i:])
                break

        return list(events[:ideal]), list(events[ideal:])

    def _make_raw_archive(self, events: List[Event], current_memory: str) -> tuple[str, str]:
        """Build a plain-text ``(history_entry, memory_update)`` without LLM.

        Called after ``MAX_FAILURES_BEFORE_RAW_ARCHIVE`` consecutive LLM failures.  The raw conversation text is
        appended to the existing long-term memory so that the session always makes progress even
        when the summarization LLM is unavailable.

        Args:
            events: The events to make a raw archive of.
            current_memory: The current memory.

        Returns:
            tuple[str, str]: The history entry and memory update.
            - history_entry: The history entry.
            - memory_update: The memory update.
        """
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        raw_conv = self._extract_conversation_text(events)
        if len(raw_conv) > self.max_summary_length:
            raw_conv = raw_conv[:self.max_summary_length] + "…"

        history_entry = (f"[{ts}] [RAW] {len(events)} events archived "
                         f"(LLM consolidation unavailable)")
        separator = f"\n\n---\n[{ts}] [RAW ARCHIVE — LLM UNAVAILABLE]\n"
        if current_memory.strip() and current_memory != "(empty)":
            memory_update = current_memory.rstrip() + separator + raw_conv
        else:
            memory_update = f"[{ts}] [RAW ARCHIVE]\n{raw_conv}"

        logger.warning("Raw archive written for %d events (no LLM summary available)", len(events))
        return history_entry, memory_update


# ---------------------------------------------------------------------------
# ClawSummarizerSessionManager
# ---------------------------------------------------------------------------


class ClawSummarizerSessionManager(SummarizerSessionManager):
    """trpc_claw-style summarizer manager that stores dual-layer :class:`SessionSummary` objects.

    Uses :class:`ClawSessionSummarizer` by default.
    ``create_session_summary`` syncs ``raw_events`` before summarizing and stores a :class:`SessionSummary`
    (with accumulated ``history_entries``) in the in-memory cache.
    ``get_session_summary`` returns a :class:`SessionSummary`.

    Args:
        model: LLM model to use for summarization.
        summarizer: Custom summarizer; defaults to :class:`ClawSessionSummarizer`.
        auto_summarize: Enable automatic summarization trigger.
        storage_manager: The storage manager to use for memory.
    """

    def __init__(
        self,
        model: LLMModel,
        storage_manager: StorageManager,
        summarizer: Optional[ClawSessionSummarizer] = None,
        auto_summarize: bool = True,
        **kwargs,
    ) -> None:
        """Initialize the claw summarizer session manager.

        Args:
            model: LLM model to use for summarization.
            storage_manager: The storage manager to use for memory.
            summarizer: The summarizer to use.
            auto_summarize: Enable automatic summarization trigger.
            **kwargs: Additional keyword arguments.
        """
        if summarizer is None and model:
            summarizer = ClawSessionSummarizer(model=model, storage_manager=storage_manager, **kwargs)
        super().__init__(
            model=model,
            summarizer=summarizer,
            auto_summarize=auto_summarize,
        )
        self._storage_manager: StorageManager = storage_manager

    @property
    def storage_manager(self) -> StorageManager:
        """Get the storage manager."""
        return self._storage_manager

    async def create_session_summary(
        self,
        session: Session,
        force: bool = False,
        ctx: InvocationContext = None,
    ) -> None:
        """Summarize *session*, preserving ``events`` and accumulating history.

        Args:
            session: The session to summarize.
            force: Whether to force summarization.
            ctx: The invocation context.
        """
        if not ctx:
            raise ValueError("Invocation context is required")
        if self._storage_manager and not session.events:
            memory_key = get_memory_key(session)
            persisted_memory = await self._storage_manager.read_long_term(memory_key)
            if persisted_memory.strip():
                restored_event = Event(
                    invocation_id="memory_restored",
                    author="system",
                    content=Content(
                        parts=[Part.from_text(text=persisted_memory)],
                        role="system",
                    ),
                    timestamp=time.time(),
                )
                ctx.agent_context.with_metadata(LONG_TERM_MEMORY_KEY, restored_event)
                logger.info(
                    "Session %s: restored long-term memory from MEMORY.md (%d chars)",
                    session.id,
                    len(persisted_memory),
                )

        any_progress = False
        max_rounds = MAX_CONSOLIDATION_ROUNDS

        for round_idx in range(max_rounds):
            should_run = await self.should_summarize_session(session) or (force and round_idx == 0)
            if not should_run:
                break

            original_count = len(session.events)

            # Delegate one compression round to ClawSessionSummarizer.
            history_entry = await self._summarizer.create_session_summary(session, ctx)

            if history_entry is None:
                logger.debug(
                    "Session %s: round %d produced no history_entry — stopping",
                    session.id,
                    round_idx + 1,
                )
                break

            any_progress = True
            memory_update_event: Optional[Event] = ctx.agent_context.get_metadata(LONG_TERM_MEMORY_KEY, None)
            if memory_update_event:
                memory_update = memory_update_event.content.parts[0].text
            else:
                memory_update = ""

            old_events_event: Optional[Event] = ctx.agent_context.get_metadata(RAW_EVENTS_KEY, [])
            self._update_summary_cache(
                session=session,
                history_entry=history_entry,
                memory_update=memory_update,
                original_event_count=original_count,
                compressed_event_count=len(old_events_event),
            )

            logger.debug(
                "Session %s: consolidation round %d/%d complete",
                session.id,
                round_idx + 1,
                max_rounds,
            )

        if not any_progress:
            return

        existing = self._summarizer_cache.get(session.app_name, {}).get(session.user_id, {}).get(session.id)
        if isinstance(existing, SessionSummary) and existing.metadata.get(HISTORY_KEY):
            ctx.agent_context.with_metadata(HISTORY_KEY, existing.metadata.get(HISTORY_KEY))

        if self._base_service is not None:
            if not get_invocation_ctx():
                set_invocation_ctx(ctx)
            await self._base_service.update_session(session)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_summary_cache(
        self,
        session: Session,
        history_entry: str,
        memory_update: str,
        original_event_count: int,
        compressed_event_count: int,
    ) -> None:
        """Upsert the :class:`SessionSummary` cache entry for *session*.

        Args:
            session: The session to update.
            history_entry: The history entry.
            memory_update: The memory update.
            original_event_count: The original event count.
            compressed_event_count: The compressed event count.
        """
        app_name = session.app_name
        user_id = session.user_id

        self._summarizer_cache.setdefault(app_name, {})
        self._summarizer_cache[app_name].setdefault(user_id, {})

        metadata = {
            HISTORY_KEY: history_entry,
            LONG_TERM_MEMORY_KEY: memory_update,
        }
        self._summarizer_cache[app_name][user_id][session.id] = SessionSummary(
            session_id=session.id,
            summary_text=history_entry,
            original_event_count=original_event_count,
            compressed_event_count=compressed_event_count,
            summary_timestamp=time.time(),
            metadata=metadata,
        )

    async def get_session_summary(self, session: Session) -> Optional[SessionSummary]:
        """Return the :class:`SessionSummary` for *session*, or *None*.

        Args:
            session: The session to get the summary for.

        Returns:
            Optional[SessionSummary]: The session summary, or None if not found.
        """
        result = await super().get_session_summary(session)
        if isinstance(result, SessionSummary):
            return result
        return None

    def get_summarizer_metadata(self) -> Dict[str, Any]:
        """Return summarizer metadata (delegates to the underlying summarizer).

        Returns:
            Dict[str, Any]: The summarizer metadata.
        """
        base = super().get_summarizer_metadata()
        base["summarizer_type"] = "SessionSummarizer"
        return base
