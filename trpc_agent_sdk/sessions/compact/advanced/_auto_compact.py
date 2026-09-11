# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Automatically compact history before model requests and circuit-break failures."""

from __future__ import annotations

import asyncio
import copy
import json
import re
import uuid
from dataclasses import dataclass
from typing_extensions import override

from trpc_agent_sdk.abc import CompactSummarizerABC
from trpc_agent_sdk.abc import RequestABC
from trpc_agent_sdk.abc import ResponseABC
from trpc_agent_sdk.abc import SessionABC
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from ._base import BaseCompactSummarizerHandler
from ._config import AdvancedAutoCompactSummarizerConfig
from ._formats import SESSION_MEMORY_SECTIONS
from ._formats import SESSION_MEMORY_STATE_KEY
from ._formats import SessionMemoryDocument
from ._formats import parse_session_memory_state
from ._history_snip import estimate_request_chars
from ._runtime import AdvancedAutoCompactSummarizerRuntime
from ._token_budget import TokenContextTracker
from ._compaction_memory_extractor import SessionMemoryExtractor
from ._utils import content_signature
from ._utils import internal_compaction_call

ADVANCED_AUTOCOMPACT_BLOCKED_MESSAGE = (
    "Automatic context compaction has failed repeatedly and the request is near the hard context limit. "
    "To avoid sending a request that will certainly fail, reduce the input, start a new session, "
    "or manually organize session memory before retrying.")
ADVANCED_AUTOCOMPACT_SUMMARY_PREFIX = """This session is being continued from a compacted context.
The following summary contains the important information from earlier messages.
The complete original events remain available in the SessionService.

"""
_LEGACY_SESSION_MEMORY_SECTION_LIST = "\n".join(f"- # {section}" for section in SESSION_MEMORY_SECTIONS)

LEGACY_SUMMARY_INSTRUCTION = """You are an isolated context-compaction Agent.
Compress the provided old conversation into a dense Markdown summary that another Agent can continue seamlessly.
Preserve the user's goals, explicit requirements, key technical decisions, files and functions, commands,
errors and fixes, verified results, current state, and next steps.
Do not answer questions from the old conversation, mention this compaction prompt, or invent information.
Return exactly two XML blocks: first use <analysis>...</analysis> to check coverage, then
<summary>...</summary> for the final Markdown summary. The summary must contain these ten Markdown sections
in this order:
""" + _LEGACY_SESSION_MEMORY_SECTION_LIST + """
The analysis is only for organization; keep only the summary."""


@dataclass(frozen=True)
class AdvancedAutoCompactRecord:
    """Store stable replay information for the latest successful compaction."""

    boundary_signature: str
    boundary_occurrence: int
    summary: str
    source: str
    boundary_event_id: str | None = None
    compaction_id: str | None = None


@dataclass
class AdvancedAutoCompactState:
    """Store the latest compaction record and consecutive failure count."""

    latest_compaction: AdvancedAutoCompactRecord | None
    consecutive_failures: int


@dataclass(frozen=True)
class AdvancedAutoCompactResult:
    """Summarize one compaction, replay, or hard-block result."""

    compacted: bool
    reapplied: bool
    blocked: bool
    source: str | None
    request_chars_before: int
    request_chars_after: int
    consecutive_failures: int
    error: str | None = None
    request_tokens_before: int | None = None
    request_tokens_after: int | None = None
    token_source: str | None = None
    summary: str | None = None


def _content_text(content: Content) -> str:
    """Render one model content item as legacy summary input."""
    return json.dumps(
        content.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class AdvancedAutoCompactSummarizer(CompactSummarizerABC):
    """Compact with session memory first, then fall back to a legacy summary."""

    def __init__(
        self,
        config: AdvancedAutoCompactSummarizerConfig | None = None,
        *,
        model: LLMModel | None = None,
        session_memory_extractor: SessionMemoryExtractor | None = None,
    ) -> None:
        """Initialize the compressor, summary generator, and session locks."""
        self._model = model
        self._runtime = AdvancedAutoCompactSummarizerRuntime(config=config or AdvancedAutoCompactSummarizerConfig())
        self._auto_compact_config = config.auto_compact
        self._session_memory_extractor = self._create_extractor(session_memory_extractor)
        self._states: dict[str, AdvancedAutoCompactState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._scoped_processors: dict[object, "AdvancedAutoCompactSummarizer"] = {}

    def _resolve_model(self, ctx: InvocationContext) -> LLMModel:
        """Resolve the model used for legacy compaction."""
        if self._model is not None:
            return self._model
        if ctx.agent is None:
            raise ValueError("Autocompact summary generator cannot resolve an LLM model")
        return ctx.agent.model

    async def _generate_summary(self, history: str, ctx: InvocationContext | None = None) -> str:
        """Generate a summary using the LLM model.

        Args:
            history: The conversation text to summarize

        Returns:
            Generated summary text
        """
        request = LlmRequest()
        request.append_instructions([LEGACY_SUMMARY_INSTRUCTION])
        prompt = ("Compress the following old conversation. The input may contain JSON representations "
                  "of tool calls and results:\n\n"
                  f"<history>\n{history}\n</history>")
        request.contents.append(Content(role="user", parts=[Part.from_text(text=prompt)]))

        output = ""
        with internal_compaction_call(getattr(ctx, "agent_context", None)):
            async for llm_response in self._resolve_model(ctx).generate_async(
                    request,
                    stream=False,
                    ctx=ctx,
            ):
                if llm_response.content and llm_response.content.parts:
                    for part in llm_response.content.parts:
                        if part.text:
                            output += part.text
        output = output.strip()
        if not output:
            raise ValueError("AdvancedAutoCompactSummarizer returned no final content")
        summary_match = re.search(
            r"<summary>\s*(.*?)\s*</summary>",
            output,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if summary_match is None or not summary_match.group(1).strip():
            raise ValueError("AdvancedAutoCompactSummarizer returned no <summary> block")
        return summary_match.group(1).strip()

    def _create_extractor(self,
                          session_memory_extractor: SessionMemoryExtractor | None = None) -> SessionMemoryExtractor:
        """Create the session memory extractor."""
        if session_memory_extractor is not None:
            return session_memory_extractor
        return SessionMemoryExtractor(
            runtime=self._runtime,
            model=self._model,
        )

    @property
    def session_memory_extractor(self) -> SessionMemoryExtractor:
        """Return the session memory extractor."""
        return self._session_memory_extractor

    @property
    def runtime(self) -> AdvancedAutoCompactSummarizerRuntime:
        """Return the runtime bound to this compressor."""
        return self._runtime

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        """Return the unique compaction lock for a session."""
        key = self._runtime.session_key(session_id)
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    async def _load_state(self, session_id: str) -> AdvancedAutoCompactState:
        """Restore process-local compaction state."""
        state_key = self._runtime.session_key(session_id)
        state = self._states.get(state_key)
        if state is not None:
            return state
        state = AdvancedAutoCompactState(latest_compaction=None, consecutive_failures=0)
        self._states[state_key] = state
        return state

    def _summary_content(self, summary: str) -> Content:
        """Wrap a compaction summary in stable model-visible user content."""
        return Content(
            role="user",
            parts=[Part.from_text(text=ADVANCED_AUTOCOMPACT_SUMMARY_PREFIX + summary)],
        )

    def _summary_with_recovery_path(self, summary: str) -> str:
        """Tell the model where the authoritative compacted data lives."""
        return (f"{summary.rstrip()}\n\n"
                "For exact content from before compaction, read the original "
                "SessionService Events. Current session memory is stored in "
                f"session.state[{SESSION_MEMORY_STATE_KEY!r}].")

    def _find_signature_index(
        self,
        contents: list[Content],
        signature: str,
        occurrence: int,
    ) -> int | None:
        """Locate a persisted compaction boundary by signature occurrence."""
        seen = 0
        for index, content in enumerate(contents):
            if content_signature(contents[index]) == signature:
                seen += 1
                if seen == occurrence:
                    return index
        return None

    def _find_last_signature_index(
        self,
        contents: list[Content],
        signature: str,
    ) -> int | None:
        """Find the newest matching boundary after an earlier replay."""
        for index in range(len(contents) - 1, -1, -1):
            if content_signature(contents[index]) == signature:
                return index
        return None

    def _signature_occurrence(
        self,
        contents: list[Content],
        signature: str,
        boundary_index: int,
    ) -> int:
        """Count a boundary signature's occurrences from the request start."""
        return sum(1 for content in contents[:boundary_index + 1] if content_signature(content) == signature)

    def _adjust_start_for_tool_pairing(self, contents: list[Content], start: int) -> int:
        """Extend the retained range to keep calls paired with responses."""
        if start <= 0 or start >= len(contents):
            return max(0, start)
        response_ids = {
            getattr(part.function_response, "id", None)
            for content in contents[start:]
            for part in content.parts or [] if part.function_response is not None
        }
        response_ids.discard(None)
        if not response_ids:
            return start
        for index in range(start - 1, -1, -1):
            call_ids = {
                getattr(part.function_call, "id", None)
                for part in contents[index].parts or [] if part.function_call is not None
            }
            if call_ids & response_ids:
                start = index
                response_ids -= call_ids
                if not response_ids:
                    break
        return start

    def _compaction_start(self, contents: list[Content], boundary_index: int) -> int:
        """Return the retained-content start for a legacy compaction."""
        start = min(
            boundary_index + 1,
            len(contents) - self._auto_compact_config.keep_recent_contents,
        )
        return self._adjust_start_for_tool_pairing(contents, start)

    def _session_memory_compaction_start(self, boundary_index: int) -> int:
        """Drop everything through the session-memory checkpoint boundary."""
        return boundary_index + 1

    def _apply_record(self, request: LlmRequest, record: AdvancedAutoCompactRecord) -> bool:
        """Replay a persisted compaction record into a rebuilt request."""
        boundary_index = self._find_signature_index(
            request.contents,
            record.boundary_signature,
            record.boundary_occurrence,
        )
        if boundary_index is None:
            return False
        start = (self._session_memory_compaction_start(boundary_index)
                 if record.source == "session-memory" else self._compaction_start(request.contents, boundary_index))
        request.contents = [
            self._summary_content(record.summary),
            *request.contents[start:],
        ]
        return True

    async def _latest_session_memory_record(
        self,
        ctx: InvocationContext,
    ) -> tuple[str, str, int, str] | None:
        """Read Session Memory and its checkpoint from Session.state."""
        state = ctx.session.state
        parsed = parse_session_memory_state(state.get(SESSION_MEMORY_STATE_KEY))
        if parsed is None:
            return None
        document, checkpoint, _ = parsed
        signature = checkpoint.get("boundary_signature")
        occurrence = checkpoint.get("boundary_occurrence")
        event_id = checkpoint.get("last_event_id")
        if (not isinstance(signature, str) or not isinstance(occurrence, int) or occurrence <= 0
                or not isinstance(event_id, str)):
            return None
        memory = document.to_markdown()
        if memory.strip() == SessionMemoryDocument().to_markdown().strip():
            return None
        return memory, signature, occurrence, event_id

    def _compact_with_summary(
        self,
        request: LlmRequest,
        *,
        summary: str,
        boundary_index: int,
        source: str,
        strict_boundary: bool = False,
        boundary_event_id: str | None = None,
    ) -> AdvancedAutoCompactRecord:
        """Replace the old prefix with a summary and return a replay record."""
        boundary_signature = content_signature(request.contents[boundary_index])
        boundary_occurrence = self._signature_occurrence(
            request.contents,
            boundary_signature,
            boundary_index,
        )
        start = (self._session_memory_compaction_start(boundary_index) if strict_boundary else self._compaction_start(
            request.contents, boundary_index))
        request.contents = [self._summary_content(summary), *request.contents[start:]]
        return AdvancedAutoCompactRecord(
            boundary_signature,
            boundary_occurrence,
            summary,
            source,
            boundary_event_id,
            f"advanced_autocompact:{uuid.uuid4().hex}",
        )

    def _resolve_boundary_event_id(
        self,
        ctx: InvocationContext,
        signature: str,
        occurrence: int,
    ) -> str | None:
        """Map one request-content boundary back to an active Session Event."""
        seen = 0
        for event in ctx.session.events:
            content = getattr(event, "content", None)
            if content is None or content_signature(content) != signature:
                continue
            seen += 1
            if seen == occurrence:
                event_id = getattr(event, "id", None)
                return event_id if isinstance(event_id, str) and event_id else None
        return None

    def _legacy_boundary_event_id(self, ctx: InvocationContext) -> str | None:
        """Choose a stable active-Event boundary for legacy compaction."""
        content_events = [event for event in ctx.session.events if event.content is not None]
        if len(content_events) <= 1:
            return None
        keep_count = min(
            self._auto_compact_config.keep_recent_contents,
            len(content_events) - 1,
        )
        boundary_index = len(content_events) - keep_count - 1
        start = self._compaction_start(
            [event.content for event in content_events],
            boundary_index,
        )
        event_id = getattr(content_events[max(0, start - 1)], "id", None)
        return event_id if isinstance(event_id, str) and event_id else None

    async def _persist_session_compaction(
        self,
        ctx: InvocationContext,
        record: AdvancedAutoCompactRecord,
    ) -> None:
        """Persist the compacted active window through the original SessionService."""
        compact_events = ctx.session.compact_events
        if not callable(compact_events):
            # AdvancedAutoCompactSummarizer remains usable as a request-only primitive in unit
            # tests and custom integrations. The standard Manager supplies
            # the framework Session and persists the compacted window.
            return

        boundary_event_id = record.boundary_event_id or self._resolve_boundary_event_id(
            ctx,
            record.boundary_signature,
            record.boundary_occurrence,
        )
        if boundary_event_id is None:
            raise ValueError("Cannot map the AdvancedAutoCompactSummarizer boundary to an active Session Event")

        compaction_id = record.compaction_id or f"advanced_autocompact:{uuid.uuid4().hex}"
        summary_event = Event(
            invocation_id="summary",
            author="system",
            content=self._summary_content(record.summary),
            custom_metadata={
                "session_compaction_source": record.source,
                "session_compaction_boundary_signature": record.boundary_signature,
                "session_compaction_boundary_occurrence": record.boundary_occurrence,
            },
        )
        active_before = list(ctx.session.events)
        historical_before = list(ctx.session.historical_events)
        last_update_before = ctx.session.last_update_time
        try:
            changed = compact_events(
                summary_event,
                boundary_event_id,
                compaction_id=compaction_id,
            )
            if changed:
                await ctx.session_service.update_session(ctx.session)
        except Exception:
            ctx.session.events = active_before
            ctx.session.historical_events = historical_before
            ctx.session.last_update_time = last_update_before
            raise

    def _bounded_history(self, contents: list[Content]) -> str:
        """Bound old history to the configured summary-input character limit."""
        rendered = "\n".join(f"<content>\n{_content_text(content)}\n</content>" for content in contents)
        limit = self._auto_compact_config.summary_input_max_chars
        if len(rendered) <= limit:
            return rendered
        marker = "\n...[middle of old history omitted due to the summary input limit]...\n"
        first_size = max(1, (limit - len(marker)) // 3)
        last_size = max(1, limit - len(marker) - first_size)
        return rendered[:first_size] + marker + rendered[-last_size:]

    async def _legacy_summary(
        self,
        contents: list[Content],
        ctx: InvocationContext,
    ) -> str:
        """Shrink old history across retries and generate a legacy summary."""
        retries = self._auto_compact_config.summary_retries_count
        working = list(contents)
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                return await self._generate_summary(
                    self._bounded_history(working),
                    ctx,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if len(working) <= 1:
                    break
                drop_count = max(1, len(working) // (retries - attempt + 1))
                working = working[drop_count:]
        raise RuntimeError("Legacy autocompact summary failed after retries") from last_error

    def _request_from_events(self, events: list[ResponseABC]) -> LlmRequest:
        """Build the model-visible request view used by end-of-turn compaction."""
        contents: list[Content] = []
        for event in events:
            is_model_visible = getattr(event, "is_model_visible", None)
            if callable(is_model_visible) and not is_model_visible():
                continue
            content = getattr(event, "content", None)
            if content is not None:
                contents.append(content.model_copy(deep=True))
        return LlmRequest(contents=contents)

    @override
    async def should_summarize(self, session: SessionABC) -> bool:
        """Check the character threshold without mutating the Session."""
        if not self._auto_compact_config.enabled:
            return False
        request = self._request_from_events(list(getattr(session, "events", []) or []))
        if len(request.contents) <= self._auto_compact_config.keep_recent_contents:
            return False

        token_config = self._runtime.config.token_context_tracker
        if token_config.enabled and token_config.model_context_window_tokens is not None:
            effective_window = token_config.model_context_window_tokens - token_config.max_output_tokens
            threshold = int(effective_window * token_config.auto_compact_ratio)
            return TokenContextTracker(token_config).estimate_request_tokens(request) >= threshold
        return estimate_request_chars(request) >= self._auto_compact_config.trigger_chars

    @override
    async def create_session_summary_by_events(
        self,
        events: list[ResponseABC],
        session_id: str,
        keep_recent_count: int = 10,
        ctx: InvocationContext | None = None,
        historical_events: list[ResponseABC] | None = None,
        store_historical_events: bool = False,
    ) -> tuple[str | None, list[ResponseABC]]:
        """Compact Events through the existing request-compaction algorithm."""
        del keep_recent_count
        if ctx is None:
            raise ValueError("Invocation context is required for advanced compaction")
        if session_id != ctx.session_id:
            raise ValueError("Session ID does not match the invocation context")

        request = self._request_from_events(events)
        result = await self.apply(request, ctx=ctx, force=True)
        if result.summary is not None:
            events[:] = list(ctx.session.events)
            if store_historical_events and historical_events is not None:
                historical_events[:] = list(ctx.session.historical_events)
        return result.summary, events

    @override
    async def create_session_summary(
        self,
        session: SessionABC,
        ctx: InvocationContext | None = None,
        store_historical_events: bool = False,
    ) -> str | None:
        """Compact one Session and persist its active and historical Events."""
        if ctx is None:
            raise ValueError("Invocation context is required for advanced compaction")
        events = getattr(session, "events", None)
        historical_events = getattr(session, "historical_events", None)
        if not isinstance(events, list) or not isinstance(historical_events, list):
            raise TypeError("Advanced compaction requires a Session with Event history")
        summary, _ = await self.create_session_summary_by_events(
            events,
            session.id,
            ctx=ctx,
            historical_events=historical_events,
            store_historical_events=store_historical_events,
        )
        return summary

    @override
    async def create_session_summary_by_request(
        self,
        request: RequestABC,
        ctx: InvocationContext | None = None,
        force: bool = False,
    ) -> LlmResponse | None:
        """Compact a built model request immediately before generation."""
        if ctx is None:
            raise ValueError("Invocation context is required for advanced compaction")
        if not isinstance(request, LlmRequest):
            raise TypeError("Advanced compaction requires an LlmRequest")

        result = await self.apply(request, ctx=ctx, force=force)
        if not result.blocked:
            TokenContextTracker.record_request_context(request, ctx)
            return None
        return LlmResponse(content=Content(
            role="model",
            parts=[Part.from_text(text=ADVANCED_AUTOCOMPACT_BLOCKED_MESSAGE)],
        ))

    @override
    def get_summary_metadata(self) -> dict[str, object]:
        """Return advanced compaction configuration metadata."""
        return {
            "strategy": "advanced",
            "auto_compact_enabled": self._auto_compact_config.enabled,
            "trigger_chars": self._auto_compact_config.trigger_chars,
            "keep_recent_contents": self._auto_compact_config.keep_recent_contents,
        }

    async def apply(
        self,
        request: LlmRequest,
        *,
        ctx: InvocationContext,
        force: bool = False,
    ) -> AdvancedAutoCompactResult:
        """Run compaction against the current session's tenant namespace."""
        session_id = ctx.session_id
        if self._runtime.scope:
            return await self._apply_scoped(request, session_id=session_id, ctx=ctx, force=force)
        runtime = self._runtime.for_session(ctx.session)
        processor = self._scoped_processors.get(runtime.scope)
        if processor is None:
            processor = copy.copy(self)
            processor._runtime = runtime
            processor._states = {}
            processor._session_locks = {}
            self._scoped_processors[runtime.scope] = processor
        return await processor.apply(request, ctx=ctx, force=force)

    async def _apply_scoped(
        self,
        request: LlmRequest,
        *,
        session_id: str,
        ctx: InvocationContext,
        force: bool = False,
    ) -> AdvancedAutoCompactResult:
        """Replay old compaction and compact again when pressure is high."""
        config = self._runtime.config
        auto_compact_config = config.auto_compact
        tracker = TokenContextTracker(config.token_context_tracker)
        if not auto_compact_config.enabled:
            request_chars = estimate_request_chars(request)
            return AdvancedAutoCompactResult(compacted=False,
                                             reapplied=False,
                                             blocked=False,
                                             source=None,
                                             request_chars_before=request_chars,
                                             request_chars_after=request_chars,
                                             consecutive_failures=0,
                                             request_tokens_before=None,
                                             request_tokens_after=None,
                                             token_source=None)
        async with self._session_lock(session_id):
            request.contents = [content.model_copy(deep=True) for content in request.contents]
            state = await self._load_state(session_id)
            reapplied = False
            if state.latest_compaction is not None:
                reapplied = self._apply_record(request, state.latest_compaction)

            request_chars_before = estimate_request_chars(request)
            token_budget_before = tracker.budget(request, ctx)
            token_mode = token_budget_before.token_mode_enabled
            request_tokens_before = token_budget_before.estimate.tokens
            comparison_tokens_before = (tracker.estimate_request_tokens(request) if token_mode else None)
            blocking_reached = (request_tokens_before >= token_budget_before.blocking_threshold_tokens
                                if token_mode else request_chars_before >= self._auto_compact_config.blocking_chars)
            if state.consecutive_failures >= auto_compact_config.max_failures and blocking_reached:
                return AdvancedAutoCompactResult(
                    compacted=False,
                    reapplied=reapplied,
                    blocked=True,
                    source=None,
                    request_chars_before=request_chars_before,
                    request_chars_after=request_chars_before,
                    consecutive_failures=state.consecutive_failures,
                )
            if state.consecutive_failures >= auto_compact_config.max_failures:
                return AdvancedAutoCompactResult(
                    compacted=False,
                    reapplied=reapplied,
                    blocked=False,
                    source=None,
                    request_chars_before=request_chars_before,
                    request_chars_after=request_chars_before,
                    consecutive_failures=state.consecutive_failures,
                )
            auto_compact_reached = (request_tokens_before >= token_budget_before.auto_compact_threshold_tokens
                                    if token_mode else request_chars_before >= auto_compact_config.trigger_chars)
            if not force and not auto_compact_reached:
                return AdvancedAutoCompactResult(
                    compacted=False,
                    reapplied=reapplied,
                    blocked=False,
                    source=state.latest_compaction.source if reapplied and state.latest_compaction else None,
                    request_chars_before=request_chars_before,
                    request_chars_after=request_chars_before,
                    consecutive_failures=state.consecutive_failures,
                )

            original_contents = [content.model_copy(deep=True) for content in request.contents]
            try:
                compact_record: AdvancedAutoCompactRecord | None = None
                if self._session_memory_extractor is not None:
                    await self._session_memory_extractor.extract_if_needed(
                        ctx,
                        force=True,
                    )
                session_memory = await self._latest_session_memory_record(ctx, )
                if session_memory is not None:
                    memory, boundary_signature, boundary_occurrence, boundary_event_id = session_memory
                    boundary_index = self._find_signature_index(
                        request.contents,
                        boundary_signature,
                        boundary_occurrence,
                    )
                    if boundary_index is None and reapplied:
                        boundary_index = self._find_last_signature_index(
                            request.contents,
                            boundary_signature,
                        )
                    if boundary_index is not None:
                        compact_record = self._compact_with_summary(
                            request,
                            summary=self._summary_with_recovery_path(memory),
                            boundary_index=boundary_index,
                            source="session-memory",
                            strict_boundary=True,
                            boundary_event_id=boundary_event_id,
                        )
                        if token_mode:
                            target_reached = (tracker.budget(request, ctx).estimate.tokens
                                              <= token_budget_before.warning_threshold_tokens)
                        else:
                            target_reached = estimate_request_chars(request) <= auto_compact_config.target_chars
                        if not target_reached:
                            request.contents = [content.model_copy(deep=True) for content in original_contents]
                            compact_record = None

                if compact_record is None:
                    keep_count = min(
                        auto_compact_config.keep_recent_contents,
                        max(1,
                            len(request.contents) - 1),
                    )
                    boundary_index = len(request.contents) - keep_count - 1
                    if boundary_index < 0:
                        raise ValueError("Not enough model contents to compact")
                    summary = await self._legacy_summary(
                        request.contents[:boundary_index + 1],
                        ctx,
                    )
                    compact_record = self._compact_with_summary(
                        request,
                        summary=self._summary_with_recovery_path(summary),
                        boundary_index=boundary_index,
                        source="legacy",
                        boundary_event_id=self._legacy_boundary_event_id(ctx),
                    )

                request_chars_after = estimate_request_chars(request)
                if token_mode:
                    comparison_tokens_after = tracker.estimate_request_tokens(request)
                    if (comparison_tokens_after >= comparison_tokens_before
                            and request_chars_after >= request_chars_before):
                        raise ValueError("Advanced Auto Compact did not reduce request token estimate")
                elif request_chars_after >= request_chars_before:
                    raise ValueError("Advanced Auto Compact did not reduce request size")
                await self._persist_session_compaction(ctx, compact_record)
                state.latest_compaction = compact_record
                state.consecutive_failures = 0
                return AdvancedAutoCompactResult(
                    compacted=True,
                    reapplied=reapplied,
                    blocked=False,
                    source=compact_record.source,
                    request_chars_before=request_chars_before,
                    request_chars_after=request_chars_after,
                    consecutive_failures=0,
                    request_tokens_before=comparison_tokens_before if token_mode else None,
                    request_tokens_after=comparison_tokens_after if token_mode else None,
                    token_source="estimated" if token_mode else None,
                    summary=compact_record.summary,
                )
            except Exception as exc:  # noqa: BLE001
                request.contents = original_contents
                state.consecutive_failures += 1
                blocked = state.consecutive_failures >= auto_compact_config.max_failures and blocking_reached
                return AdvancedAutoCompactResult(
                    compacted=False,
                    reapplied=reapplied,
                    blocked=blocked,
                    source=None,
                    request_chars_before=request_chars_before,
                    request_chars_after=request_chars_before,
                    consecutive_failures=state.consecutive_failures,
                    error=str(exc) if exc else None if blocked else None,
                    request_tokens_before=request_tokens_before if token_mode else None,
                    request_tokens_after=comparison_tokens_before if token_mode else None,
                    token_source=token_budget_before.estimate.source if token_mode else None,
                )


class AdvancedAutoCompactSummarizerHandler(BaseCompactSummarizerHandler):
    """Advanced auto compact summarizer handler."""

    @override
    async def handle(
        self,
        ctx: InvocationContext,
        request: LlmRequest,
        force: bool = False,
    ) -> LlmResponse | None:
        """Compact before each request and return a local block after failures."""
        summarizer = self.get_summarizer(ctx)
        if not isinstance(summarizer, AdvancedAutoCompactSummarizer):
            raise ValueError("Summarizer is not an AdvancedAutoCompactSummarizer")
        return await summarizer.create_session_summary_by_request(
            request,
            ctx=ctx,
            force=force,
        )
