# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Mechanically clean old tool results before model calls."""

from __future__ import annotations

import asyncio
import copy
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.types import Part

from ._base import BaseCompactSummarizerHandler
from ._runtime import AdvancedAutoCompactSummarizerRuntime
from ._tool_result_budget import is_budget_replacement_response
from ._tool_result_budget import serialize_tool_response
from ._tool_result_budget import stable_tool_result_id
from ._tool_result_budget import tool_result_sha256

MICROCOMPACT_CLEARED_MESSAGE = "[Old tool result content cleared]"


@dataclass
class MicroCompactState:
    """Store identifiers for mechanically cleaned tool results."""

    cleared_ids: set[str]
    result_hashes: dict[str, str]


@dataclass(frozen=True)
class MicroCompactCandidate:
    """Describe a function response eligible for mechanical cleanup."""

    result_id: str
    tool_name: str
    original_size: int
    original_sha256: str
    part: Part


@dataclass(frozen=True)
class MicroCompactResult:
    """Summarize new and repeated mechanical cleanup operations."""

    trigger: Optional[str] = field(default=None)
    cleared_count: int = field(default=0)
    reapplied_count: int = field(default=0)
    chars_saved: int = field(default=0)


def find_last_assistant_timestamp(ctx: "InvocationContext") -> float | None:
    """Find the latest real model-response time in session events."""
    for event in reversed(ctx.session.events):
        if event.author == "user" or not event.content or not event.content.parts:
            continue
        if any(part.function_response is not None or part.code_execution_result is not None
               for part in event.content.parts):
            continue
        if any(part.text is not None or part.function_call is not None for part in event.content.parts):
            return event.timestamp
    return None


class MicroCompact:
    """Local compressor that cleans old tool results by time or count."""

    def __init__(self, runtime: AdvancedAutoCompactSummarizerRuntime) -> None:
        """Initialize mechanical-compaction state and per-session locks."""
        self._runtime = runtime
        self._config = runtime.config.micro_compact
        self._states: dict[str, MicroCompactState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._scoped_processors: dict[object, MicroCompact] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        """Return the unique async compaction lock for a session."""
        key = self._runtime.session_key(session_id) if hasattr(self._runtime, "session_key") else session_id
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    async def _load_state(self, session_id: str) -> MicroCompactState:
        """Return process-local micro-compact state."""
        state_key = self._runtime.session_key(session_id) if hasattr(self._runtime, "session_key") else session_id
        state = self._states.get(state_key)
        if state is not None:
            return state
        state = MicroCompactState(
            cleared_ids=set(),
            result_hashes={},
        )
        self._states[state_key] = state
        return state

    def _collect_candidates(self, request: LlmRequest) -> list[MicroCompactCandidate]:
        """Collect eligible tool results in request order."""
        allowed_tools = set(self._config.tool_names)
        candidates: list[MicroCompactCandidate] = []
        serialized_by_result_id: dict[str, str] = {}
        for content in request.contents:
            for part in content.parts or []:
                function_response = part.function_response
                if function_response is None or function_response.name not in allowed_tools:
                    continue
                if is_budget_replacement_response(function_response.response):
                    continue
                serialized = serialize_tool_response(function_response.response)
                result_id = stable_tool_result_id(function_response.name, function_response, serialized)
                previous_result = serialized_by_result_id.get(result_id)
                if previous_result is not None and previous_result != serialized:
                    raise ValueError(f"Tool result id {result_id!r} is reused with different content")
                serialized_by_result_id[result_id] = serialized
                candidates.append(
                    MicroCompactCandidate(
                        result_id=result_id,
                        tool_name=function_response.name,
                        original_size=len(serialized),
                        original_sha256=tool_result_sha256(serialized),
                        part=part,
                    ))
        return candidates

    def _cleared_response(self) -> dict[str, str]:
        """Return the minimal placeholder shared by cleanups."""
        return {"output": MICROCOMPACT_CLEARED_MESSAGE}

    async def apply(
        self,
        request: LlmRequest,
        *,
        ctx: InvocationContext,
        last_assistant_timestamp: float | None,
        now: float | None = None,
    ) -> MicroCompactResult:
        """Clean a request copy by age first and count second."""
        if not self._config.enabled:
            return MicroCompactResult()
        if self._runtime.scope:
            return await self._apply_scoped(
                request,
                session_id=ctx.session_id,
                last_assistant_timestamp=last_assistant_timestamp,
                now=now,
            )
        runtime = self._runtime.for_session(ctx.session)
        processor = self._scoped_processors.get(runtime.scope)
        if processor is None:
            processor = copy.copy(self)
            processor._runtime = runtime
            processor._states = {}
            processor._session_locks = {}
            self._scoped_processors[runtime.scope] = processor
        return await processor.apply(
            request,
            last_assistant_timestamp=last_assistant_timestamp,
            ctx=ctx,
            now=now,
        )

    async def _apply_scoped(
        self,
        request: LlmRequest,
        *,
        session_id: str,
        last_assistant_timestamp: float | None,
        now: float | None,
    ) -> MicroCompactResult:
        """Apply one tenant-bound mechanical compaction."""
        async with self._session_lock(session_id):
            request.contents = [content.model_copy(deep=True) for content in request.contents]
            state = await self._load_state(session_id)
            candidates = self._collect_candidates(request)
            for candidate in candidates:
                known_hash = state.result_hashes.get(candidate.result_id)
                if known_hash is not None and known_hash != candidate.original_sha256:
                    raise ValueError(f"Tool result id {candidate.result_id!r} is reused with different content")

            reapplied_count = 0
            active_candidates: list[MicroCompactCandidate] = []
            for candidate in candidates:
                if candidate.result_id in state.cleared_ids:
                    candidate.part.function_response.response = self._cleared_response()
                    reapplied_count += 1
                else:
                    active_candidates.append(candidate)

            current_time = time.time() if now is None else now
            gap_seconds = current_time - last_assistant_timestamp if last_assistant_timestamp is not None else None
            if gap_seconds is not None and gap_seconds >= self._config.gap_seconds:
                trigger = "time"
            elif len(active_candidates) > self._config.trigger_count:
                trigger = "count"
            else:
                trigger = None

            if trigger is None:
                return MicroCompactResult(reapplied_count=reapplied_count)

            clear_candidates = active_candidates[:-self._config.keep_recent]
            if not clear_candidates:
                return MicroCompactResult(reapplied_count=reapplied_count)

            cleared_size = len(serialize_tool_response(self._cleared_response()))
            chars_saved = 0
            for candidate in clear_candidates:
                candidate.part.function_response.response = self._cleared_response()
                state.cleared_ids.add(candidate.result_id)
                state.result_hashes[candidate.result_id] = candidate.original_sha256
                chars_saved += max(0, candidate.original_size - cleared_size)

            return MicroCompactResult(
                trigger=trigger,
                cleared_count=len(clear_candidates),
                reapplied_count=reapplied_count,
                chars_saved=chars_saved,
            )


class MicroCompactHandler(BaseCompactSummarizerHandler):
    """Adapt the mechanical compressor to before_model_callback."""

    def __init__(self) -> None:
        """Store the compressor executed before model requests."""
        self._micro_compact: MicroCompact | None = None

    @override
    async def handle(self, ctx: InvocationContext, request: LlmRequest) -> None:
        """Calculate the time gap and run mechanical cleanup before a request."""
        summarizer = self.get_summarizer(ctx)
        from ._auto_compact import AdvancedAutoCompactSummarizer
        if not isinstance(summarizer, AdvancedAutoCompactSummarizer):
            raise ValueError("Summarizer is not an AdvancedAutoCompactSummarizer")
        if self._micro_compact is None:
            self._micro_compact = MicroCompact(summarizer.runtime)
        await self._micro_compact.apply(request, ctx=ctx, last_assistant_timestamp=find_last_assistant_timestamp(ctx))
