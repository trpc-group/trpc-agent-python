# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Limit tool-result context usage before model requests."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional
from typing_extensions import override

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.models import LlmRequest

from ..._session import Session
from ._runtime import AdvancedAutoCompactSummarizerRuntime
from ._base import BaseCompactSummarizerHandler

TOOL_RESULT_REPLACEMENT_SCHEMA_VERSION = 1


@dataclass
class ToolResultBudgetState:
    """Store processed results and stable replacements to protect the prompt prefix."""

    seen_ids: set[str]
    replacements: dict[str, dict[str, Any]]
    result_hashes: dict[str, str]


@dataclass(frozen=True)
class ToolResultCandidate:
    """Describe a function response candidate in a model request."""

    result_id: str
    tool_name: str
    serialized_result: str
    original_size: int
    part: Any
    event_id: Optional[str] = field(default=None)


@dataclass(frozen=True)
class ToolResultReplacement:
    """Describe a tool result replaced by an Event reference and preview."""

    candidate: ToolResultCandidate
    replacement_response: dict[str, Any]
    replacement_size: int


@dataclass(frozen=True)
class ToolResultBudgetResult:
    """Summarize replacements and character savings from budget processing."""

    replaced_count: int = field(default=0)
    original_chars: int = field(default=0)
    replacement_chars: int = field(default=0)


def serialize_tool_response(response: Any) -> str:
    """Serialize a tool result as stable JSON for character counting."""
    return json.dumps(
        response,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_tool_result_id(tool_name: str, function_response: Any, serialized_result: str) -> str:
    """Prefer a tool-call ID, or derive a stable key from name and result."""
    response_id = getattr(function_response, "id", None)
    if isinstance(response_id, str) and response_id:
        return response_id
    digest = hashlib.sha256(f"{tool_name}\0{serialized_result}".encode("utf-8")).hexdigest()
    return f"derived-{digest[:32]}"


def tool_result_sha256(serialized_result: str) -> str:
    """Hash the stable result body to detect cross-request ID collisions."""
    return hashlib.sha256(serialized_result.encode("utf-8")).hexdigest()


def is_budget_replacement_response(response: Any) -> bool:
    """Return whether a response is already a budget replacement."""
    if not isinstance(response, dict):
        return False
    marker = response.get("_advanced_memory")
    return isinstance(marker, dict) and marker.get("kind") == "tool-result-budget"


def _preview_text(serialized_result: str, limit: int) -> tuple[str, bool]:
    """Build a result preview within the character limit when possible."""
    if len(serialized_result) <= limit:
        return serialized_result, False
    truncated = serialized_result[:limit]
    last_newline = truncated.rfind("\n")
    if last_newline >= limit // 2:
        truncated = truncated[:last_newline]
    return truncated, True


class ToolResultBudget:
    """Apply stable, recoverable tool-result budgeting to each request."""

    def __init__(self, runtime: AdvancedAutoCompactSummarizerRuntime) -> None:
        """Initialize the budget processor and per-session state locks."""
        self._runtime: AdvancedAutoCompactSummarizerRuntime = runtime
        self._tool_result_budget_config = runtime.config.tool_result_budget
        self._states: dict[str, ToolResultBudgetState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}
        self._scoped_processors: dict[object, ToolResultBudget] = {}

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        """Return the unique async budget lock for a session."""
        key = self._runtime.session_key(session_id)
        lock = self._session_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[key] = lock
        return lock

    async def _load_state(self, session_id: str) -> ToolResultBudgetState:
        """Return process-local state for the current Session."""
        state_key = self._runtime.session_key(session_id)
        state = self._states.get(state_key)
        if state is not None:
            return state
        state = ToolResultBudgetState(
            seen_ids=set(),
            replacements={},
            result_hashes={},
        )
        self._states[state_key] = state
        return state

    def _collect_candidates(
        self,
        request: LlmRequest,
        session: Session,
    ) -> list[list[ToolResultCandidate]]:
        """Group function responses from consecutive user contents."""
        event_ids: dict[str, str] = {}
        for event in session.events:
            event_id = getattr(event, "id", None)
            if event_id is None:
                continue
            event_content = event.content
            if event_content is None:
                continue
            for event_part in event_content.parts:
                response = getattr(event_part, "function_response", None)
                response_id = response.id if response is not None else None
                if response_id is not None:
                    event_ids[response_id] = event_id
        candidate_groups: list[list[ToolResultCandidate]] = []
        serialized_by_result_id: dict[str, str] = {}
        current_group: list[ToolResultCandidate] = []
        for content in request.contents:
            if content.role in {"model", "assistant"}:
                if current_group:
                    candidate_groups.append(current_group)
                    current_group = []
                continue
            for part in content.parts or []:
                function_response = part.function_response
                if function_response is None:
                    continue
                tool_name = function_response.name or "unknown"
                serialized_result = serialize_tool_response(function_response.response)
                result_id = stable_tool_result_id(tool_name, function_response, serialized_result)
                previous_result = serialized_by_result_id.get(result_id)
                if previous_result is not None and previous_result != serialized_result:
                    raise ValueError(f"Tool result id {result_id!r} is reused with different content")
                serialized_by_result_id[result_id] = serialized_result
                current_group.append(
                    ToolResultCandidate(
                        result_id=result_id,
                        event_id=event_ids.get(result_id),
                        tool_name=tool_name,
                        serialized_result=serialized_result,
                        original_size=len(serialized_result),
                        part=part,
                    ))
        if current_group:
            candidate_groups.append(current_group)
        return candidate_groups

    def _build_replacement(
        self,
        candidate: ToolResultCandidate,
    ) -> ToolResultReplacement:
        """Build an event reference and model-visible preview."""
        preview, truncated = _preview_text(
            candidate.serialized_result,
            self._tool_result_budget_config.preview_chars,
        )
        replacement_response = {
            "_advanced_memory": {
                "kind": "tool-result-budget",
                "schema_version": TOOL_RESULT_REPLACEMENT_SCHEMA_VERSION,
            },
            "message": ("The tool result exceeded the context budget; read the referenced "
                        "Session Event when needed."),
            "session_event_id": candidate.event_id or candidate.result_id,
            "original_chars": candidate.original_size,
            "preview": preview,
            "truncated": truncated,
        }
        return ToolResultReplacement(
            candidate=candidate,
            replacement_response=replacement_response,
            replacement_size=len(serialize_tool_response(replacement_response)),
        )

    def _select_replacements(
        self,
        groups: list[list[ToolResultCandidate]],
        state: ToolResultBudgetState,
    ) -> list[ToolResultReplacement]:
        """Apply per-result limits, then select results under the aggregate limit."""
        selected: dict[str, ToolResultReplacement] = {}
        for group in groups:
            fresh = [
                candidate for candidate in group
                if candidate.result_id not in state.seen_ids and candidate.result_id not in state.replacements
            ]
            fresh_ids = {candidate.result_id for candidate in fresh}
            for candidate in fresh:
                if candidate.original_size > self._tool_result_budget_config.max_chars:
                    selected[candidate.result_id] = self._build_replacement(candidate)

            visible_size = 0
            remaining_fresh: list[ToolResultCandidate] = []
            for candidate in group:
                known_replacement = state.replacements.get(candidate.result_id)
                new_replacement = selected.get(candidate.result_id)
                if known_replacement is not None:
                    visible_size += len(serialize_tool_response(known_replacement))
                elif new_replacement is not None:
                    visible_size += new_replacement.replacement_size
                else:
                    visible_size += candidate.original_size
                    if candidate.result_id in fresh_ids:
                        remaining_fresh.append(candidate)

            for candidate in sorted(remaining_fresh, key=lambda item: item.original_size, reverse=True):
                if visible_size <= self._tool_result_budget_config.per_message_max_chars:
                    break
                replacement = self._build_replacement(candidate)
                if replacement.replacement_size >= candidate.original_size:
                    continue
                selected[candidate.result_id] = replacement
                visible_size -= candidate.original_size - replacement.replacement_size
        return list(selected.values())

    async def apply(self, request: LlmRequest, ctx: InvocationContext) -> ToolResultBudgetResult:
        """Process a model request without mutating session Events."""
        if not self._tool_result_budget_config.enabled:
            return ToolResultBudgetResult()
        if self._runtime.scope:
            return await self._apply_scoped(request, ctx.session)
        runtime = self._runtime.for_session(ctx.session)
        processor = self._scoped_processors.get(runtime.scope)
        if processor is None:
            processor = copy.copy(self)
            processor._runtime = runtime
            processor._states = {}
            processor._session_locks = {}
            self._scoped_processors[runtime.scope] = processor
        return await processor.apply(request, ctx=ctx)

    async def _apply_scoped(self, request: LlmRequest, session: Session) -> ToolResultBudgetResult:
        """Apply budgeting while ``_runtime`` is bound to the current tenant."""
        async with self._session_lock(session.id):
            request.contents = [content.model_copy(deep=True) for content in request.contents]
            state = await self._load_state(session.id)
            groups = self._collect_candidates(request, session)
            for group in groups:
                for candidate in group:
                    known_hash = state.result_hashes.get(candidate.result_id)
                    current_hash = tool_result_sha256(candidate.serialized_result)
                    if known_hash is not None and known_hash != current_hash:
                        raise ValueError(f"Tool result id {candidate.result_id!r} is reused with different content")
            selected = self._select_replacements(groups, state)

            for replacement in selected:
                state.replacements[replacement.candidate.result_id] = replacement.replacement_response
                state.result_hashes[replacement.candidate.result_id] = tool_result_sha256(
                    replacement.candidate.serialized_result)

            selected_ids = {replacement.candidate.result_id for replacement in selected}
            for group in groups:
                for candidate in group:
                    if candidate.result_id not in state.seen_ids and candidate.result_id not in selected_ids:
                        state.seen_ids.add(candidate.result_id)
                        state.result_hashes[candidate.result_id] = tool_result_sha256(candidate.serialized_result)

            original_chars = 0
            replacement_chars = 0
            selected_by_id = {replacement.candidate.result_id: replacement for replacement in selected}
            for group in groups:
                for candidate in group:
                    replacement_response = state.replacements.get(candidate.result_id)
                    if replacement_response is not None:
                        candidate.part.function_response.response = replacement_response
                    selected_replacement = selected_by_id.get(candidate.result_id)
                    if selected_replacement is not None:
                        original_chars += candidate.original_size
                        replacement_chars += selected_replacement.replacement_size
                    state.seen_ids.add(candidate.result_id)

            return ToolResultBudgetResult(
                replaced_count=len(selected),
                original_chars=original_chars,
                replacement_chars=replacement_chars,
            )


class ToolResultBudgetHandler(BaseCompactSummarizerHandler):
    """Adapt the tool-result budget processor to before_model_callback."""

    def __init__(self) -> None:
        """Store the budget processor run before model requests."""
        self._budget: ToolResultBudget | None = None

    @override
    async def handle(self, ctx: InvocationContext, request: LlmRequest) -> None:
        """Apply tool-result budgeting without truncating model calls."""
        summarizer = self.get_summarizer(ctx)
        from ._auto_compact import AdvancedAutoCompactSummarizer
        if not isinstance(summarizer, AdvancedAutoCompactSummarizer):
            raise ValueError("Summarizer is not an AdvancedAutoCompactSummarizer")
        if self._budget is None:
            self._budget = ToolResultBudget(summarizer.runtime)
        await self._budget.apply(request, ctx=ctx)
        return None
