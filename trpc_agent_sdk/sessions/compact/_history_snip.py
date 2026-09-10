# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Clean old tool results when the context nears its budget."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from typing import Any
from typing import TYPE_CHECKING

from ._callbacks import install_staged_callback
from ._runtime import SessionCompactRuntime
from ._tool_result_budget import is_budget_replacement_response
from ._tool_result_budget import serialize_tool_response
from ._tool_result_budget import stable_tool_result_id
from ._tool_result_budget import tool_result_sha256
from ._token_budget import TokenContextTracker

if TYPE_CHECKING:
    from trpc_agent_sdk.agents import LlmAgent
    from trpc_agent_sdk.context import InvocationContext
    from trpc_agent_sdk.models import LlmRequest

HISTORY_SNIP_CLEARED_MESSAGE = "[Older tool result removed by history snip]"


@dataclass
class HistorySnipState:
    """Store identifiers for tool results removed from model context."""

    snipped_ids: set[str]
    result_hashes: dict[str, str]


@dataclass(frozen=True)
class HistorySnipCandidate:
    """Describe a tool result eligible for removal from old context."""

    result_id: str
    tool_name: str
    original_size: int
    original_sha256: str
    part: Any


@dataclass(frozen=True)
class HistorySnipResult:
    """Summarize a history-snip trigger and character savings."""

    trigger: str | None
    snipped_count: int
    reapplied_count: int
    chars_saved: int
    request_chars_before: int
    request_chars_after: int
    request_tokens_before: int | None = None
    request_tokens_after: int | None = None
    token_source: str | None = None


def estimate_request_chars(request: "LlmRequest") -> int:
    """Estimate the full model request using stable JSON serialization."""
    payload = request.model_dump(
        mode="python",
        by_alias=True,
        exclude_none=True,
    )
    return len(json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ))


class HistorySnip:
    """Mechanically remove the oldest tool results when the request is too large."""

    def __init__(self, memory_runtime: SessionCompactRuntime) -> None:
        """Initialize history-snip state and per-session async locks."""
        self._runtime = memory_runtime
        self._states: dict[str, HistorySnipState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._scoped_processors: dict[object, "HistorySnip"] = {}

    @property
    def runtime(self) -> SessionCompactRuntime:
        """Return the runtime bound to this history snipper."""
        return self._runtime

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        """Return the unique history-snip lock for a session."""
        key = self._runtime.session_key(session_id) if hasattr(self._runtime, "session_key") else session_id
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    async def _load_state(self, session_id: str) -> HistorySnipState:
        """Return process-local history-snip state."""
        state_key = self._runtime.session_key(session_id) if hasattr(self._runtime, "session_key") else session_id
        state = self._states.get(state_key)
        if state is not None:
            return state
        state = HistorySnipState(
            snipped_ids=set(),
            result_hashes={},
        )
        self._states[state_key] = state
        return state

    def _collect_candidates(self, request: "LlmRequest") -> list[HistorySnipCandidate]:
        """Collect eligible function results in request order."""
        allowed_tools = set(self._runtime.config.history_snip_tool_names)
        candidates: list[HistorySnipCandidate] = []
        serialized_by_result_id: dict[str, str] = {}
        for content in request.contents:
            for part in content.parts or []:
                function_response = part.function_response
                if function_response is None or function_response.name not in allowed_tools:
                    continue
                if is_budget_replacement_response(function_response.response):
                    continue
                tool_name = function_response.name or "unknown"
                serialized = serialize_tool_response(function_response.response)
                result_id = stable_tool_result_id(tool_name, function_response, serialized)
                previous_result = serialized_by_result_id.get(result_id)
                if previous_result is not None and previous_result != serialized:
                    raise ValueError(f"Tool result id {result_id!r} is reused with different content")
                serialized_by_result_id[result_id] = serialized
                candidates.append(
                    HistorySnipCandidate(
                        result_id=result_id,
                        tool_name=tool_name,
                        original_size=len(serialized),
                        original_sha256=tool_result_sha256(serialized),
                        part=part,
                    ))
        return candidates

    def _snipped_response(self) -> dict[str, str]:
        """Return the stable placeholder used by history snip."""
        return {"output": HISTORY_SNIP_CLEARED_MESSAGE}

    async def apply(
        self,
        request: "LlmRequest",
        *,
        session_id: str,
        ctx: "InvocationContext | None" = None,
        force: bool = False,
    ) -> HistorySnipResult:
        """Clean old tool results when over budget or explicitly forced."""
        config = self._runtime.config
        if not config.enabled or not config.history_snip_enabled:
            request_chars = estimate_request_chars(request)
            return HistorySnipResult(None, 0, 0, 0, request_chars, request_chars)
        if ctx is None or hasattr(self._runtime, "scope"):
            return await self._apply_scoped(request, session_id=session_id, ctx=ctx, force=force)
        runtime = self._runtime.for_session(ctx.session)
        processor = self._scoped_processors.get(runtime.scope)
        if processor is None:
            processor = copy.copy(self)
            processor._runtime = runtime
            processor._states = {}
            processor._session_locks = {}
            self._scoped_processors[runtime.scope] = processor
        return await processor.apply(request, session_id=session_id, ctx=ctx, force=force)

    async def _apply_scoped(
        self,
        request: "LlmRequest",
        *,
        session_id: str,
        ctx: "InvocationContext",
        force: bool,
    ) -> HistorySnipResult:
        """Apply one tenant-bound history-snipping operation."""
        config = self._runtime.config
        tracker = TokenContextTracker(config)
        async with self._session_lock(session_id):
            request.contents = [content.model_copy(deep=True) for content in request.contents]
            state = await self._load_state(session_id)
            candidates = self._collect_candidates(request)
            for candidate in candidates:
                known_hash = state.result_hashes.get(candidate.result_id)
                if known_hash is not None and known_hash != candidate.original_sha256:
                    raise ValueError(f"Tool result id {candidate.result_id!r} is reused with different content")

            reapplied_count = 0
            for candidate in candidates:
                if candidate.result_id in state.snipped_ids:
                    candidate.part.function_response.response = self._snipped_response()
                    reapplied_count += 1

            request_chars_before = estimate_request_chars(request)
            token_budget_before = tracker.budget(request, ctx)
            token_mode = token_budget_before.token_mode_enabled
            current_tokens = token_budget_before.estimate.tokens
            if not force and (current_tokens <= token_budget_before.warning_threshold_tokens
                              if token_mode else request_chars_before <= config.history_snip_trigger_chars):
                return HistorySnipResult(
                    None,
                    0,
                    reapplied_count,
                    0,
                    request_chars_before,
                    request_chars_before,
                    current_tokens if token_mode else None,
                    current_tokens if token_mode else None,
                    token_budget_before.estimate.source if token_mode else None,
                )

            trigger = "force" if force else "pressure"
            protected_ids = {candidate.result_id for candidate in candidates[-config.history_snip_keep_recent:]}
            eligible = [
                candidate for candidate in candidates
                if candidate.result_id not in state.snipped_ids and candidate.result_id not in protected_ids
            ]
            replacement_size = len(serialize_tool_response(self._snipped_response()))
            current_chars = request_chars_before
            snipped_count = 0
            for candidate in eligible:
                if not force and (current_tokens <= token_budget_before.warning_threshold_tokens
                                  if token_mode else current_chars <= config.history_snip_target_chars):
                    break
                candidate_saving = max(0, candidate.original_size - replacement_size)
                if candidate_saving == 0:
                    continue
                candidate.part.function_response.response = self._snipped_response()
                state.snipped_ids.add(candidate.result_id)
                state.result_hashes[candidate.result_id] = candidate.original_sha256
                current_chars = estimate_request_chars(request)
                current_tokens = tracker.budget(request, ctx).estimate.tokens
                snipped_count += 1

            request_chars_after = estimate_request_chars(request)
            token_budget_after = tracker.budget(request, ctx)
            actual_chars_saved = max(0, request_chars_before - request_chars_after)
            return HistorySnipResult(
                trigger=trigger if snipped_count else None,
                snipped_count=snipped_count,
                reapplied_count=reapplied_count,
                chars_saved=actual_chars_saved,
                request_chars_before=request_chars_before,
                request_chars_after=request_chars_after,
                request_tokens_before=(token_budget_before.estimate.tokens if token_mode else None),
                request_tokens_after=(token_budget_after.estimate.tokens if token_mode else None),
                token_source=(token_budget_after.estimate.source if token_mode else None),
            )


class HistorySnipCallback:
    """Adapt history snip to before_model_callback."""

    advanced_memory_stage = 20

    def __init__(self, history_snip: HistorySnip) -> None:
        """Store the history-snip processor run before model requests."""
        self._history_snip = history_snip

    @property
    def history_snip(self) -> HistorySnip:
        """Return the history-snip processor used by this callback."""
        return self._history_snip

    async def __call__(self, ctx: "InvocationContext", request: "LlmRequest") -> None:
        """Run history snip before a request based on request size."""
        await self._history_snip.apply(request, session_id=ctx.session_id, ctx=ctx)
        return None


def setup_history_snip(
    agent: "LlmAgent",
    memory_runtime: SessionCompactRuntime,
) -> HistorySnip:
    """Install history snip while preserving context stage order."""
    history_snip = HistorySnip(memory_runtime)
    callback = HistorySnipCallback(history_snip)
    existing_snip = install_staged_callback(
        agent,
        callback,
        callback_type=HistorySnipCallback,
        component_attribute="history_snip",
        memory_runtime=memory_runtime,
        conflict_message="History snip is already configured with another runtime",
    )
    return existing_snip or history_snip
