"""Automatically compact history before model requests and circuit-break failures."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from typing import Protocol
from typing import TYPE_CHECKING

from trpc_agent_sdk.agents import LlmAgent
from trpc_agent_sdk.memory import InMemoryMemoryService
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from .callbacks import install_staged_callback
from .formats import SESSION_MEMORY_SECTIONS
from .formats import SessionMemoryDocument
from .history_snip import estimate_request_chars
from .runtime import AdvancedMemoryRuntime
from .token_budget import TokenContextTracker

if TYPE_CHECKING:
    from trpc_agent_sdk.agents import LlmAgent as ParentLlmAgent
    from trpc_agent_sdk.context import InvocationContext
    from trpc_agent_sdk.models import LlmRequest

AUTOCOMPACT_SCHEMA_VERSION = 1
AUTOCOMPACT_BLOCKED_MESSAGE = (
    "Automatic context compaction has failed repeatedly and the request is near the hard context limit. "
    "To avoid sending a request that will certainly fail, reduce the input, start a new session, "
    "or manually organize session memory before retrying.")
AUTOCOMPACT_SUMMARY_PREFIX = """This session is being continued from a compacted context.
The following summary contains the important information from earlier messages.
The complete original events remain available in the session transcript.

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
class AutoCompactRecord:
    """Store stable replay information for the latest successful compaction."""

    boundary_signature: str
    boundary_occurrence: int
    summary: str
    source: str


@dataclass
class AutoCompactState:
    """Store the latest compaction record and consecutive failure count."""

    latest_compaction: AutoCompactRecord | None
    consecutive_failures: int


@dataclass(frozen=True)
class AutoCompactResult:
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


class LegacySummaryGenerator(Protocol):
    """Define the replaceable legacy compaction summary interface."""

    async def generate(self, history: str, ctx: "InvocationContext") -> str:
        """Return a workable Markdown summary for bounded old history."""


def content_signature(content: Content) -> str:
    """Generate a stable signature that preserves message identity."""
    parts: list[dict[str, Any]] = []
    for part in content.parts or []:
        if part.text is not None:
            parts.append({
                "type": "text",
                "sha256": hashlib.sha256(part.text.encode("utf-8")).hexdigest(),
            })
        elif part.function_call is not None:
            parts.append({
                "type": "function_call",
                "id": getattr(part.function_call, "id", None),
                "name": part.function_call.name,
            })
        elif part.function_response is not None:
            parts.append({
                "type": "function_response",
                "id": getattr(part.function_response, "id", None),
                "name": part.function_response.name,
            })
        elif part.executable_code is not None:
            parts.append({"type": "executable_code"})
        elif part.code_execution_result is not None:
            parts.append({"type": "code_execution_result"})
        else:
            parts.append({"type": "other"})
    serialized = json.dumps(
        {
            "role": content.role,
            "parts": parts
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _content_text(content: Content) -> str:
    """Render one model content item as legacy summary input."""
    return json.dumps(
        content.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class ForkedLegacySummaryGenerator:
    """Call a tool-free legacy summary Agent through an isolated Runner."""

    def __init__(self, model: Any | None = None) -> None:
        """Store an optional dedicated model, falling back to the parent model."""
        self._model = model

    def _resolve_model(self, ctx: "InvocationContext") -> Any:
        """Resolve the model used for legacy compaction."""
        model = self._model or getattr(ctx.agent, "model", None)
        if not model:
            raise ValueError("Autocompact summary generator cannot resolve an LLM model")
        return model

    async def generate(self, history: str, ctx: "InvocationContext") -> str:
        """Generate a summary in a temporary session without parent callbacks."""
        config = ctx.agent.generate_content_config if isinstance(ctx.agent, LlmAgent) else None
        agent = LlmAgent(
            name="advanced_autocompact_summarizer",
            description="Generate an isolated context-compaction summary.",
            instruction=LEGACY_SUMMARY_INSTRUCTION,
            model=self._resolve_model(ctx),
            tools=[],
            generate_content_config=config,
            add_name_to_instruction=False,
        )
        app_name = f"{ctx.app_name}_advanced_autocompact"
        runner = Runner(
            app_name=app_name,
            agent=agent,
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            enable_post_turn_processing=False,
        )
        last_event = None
        try:
            session = await runner.session_service.create_session(
                app_name=app_name,
                user_id="advanced-autocompact",
                state={},
            )
            prompt = ("Compress the following old conversation. The input may contain JSON representations "
                      "of tool calls and results:\n\n"
                      f"<history>\n{history}\n</history>")
            async for event in runner.run_async(
                    user_id=session.user_id,
                    session_id=session.id,
                    new_message=Content(role="user", parts=[Part.from_text(text=prompt)]),
            ):
                if not event.partial:
                    last_event = event
        finally:
            await runner.close()
        if not last_event or not last_event.content or not last_event.content.parts:
            raise ValueError("Autocompact summary generator returned no final content")
        output = "\n".join(part.text for part in last_event.content.parts if part.text).strip()
        summary_match = re.search(
            r"<summary>\s*(.*?)\s*</summary>",
            output,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if summary_match is None or not summary_match.group(1).strip():
            raise ValueError("Autocompact summary generator returned no <summary> block")
        return summary_match.group(1).strip()


class AutoCompact:
    """Compact with session memory first, then fall back to a legacy summary."""

    def __init__(
        self,
        memory_runtime: AdvancedMemoryRuntime,
        summary_generator: LegacySummaryGenerator | None = None,
        *,
        model: Any | None = None,
    ) -> None:
        """Initialize the compressor, summary generator, and session locks."""
        if summary_generator is not None and model is not None:
            raise ValueError("Provide either summary_generator or model, not both")
        self._runtime = memory_runtime
        self._summary_generator = summary_generator or ForkedLegacySummaryGenerator(model)
        self._states: dict[str, AutoCompactState] = {}
        self._session_locks: dict[str, asyncio.Lock] = {}

    @property
    def runtime(self) -> AdvancedMemoryRuntime:
        """Return the runtime bound to this compressor."""
        return self._runtime

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        """Return the unique compaction lock for a session."""
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def _load_state(self, session_id: str) -> AutoCompactState:
        """Restore the latest compaction and failure count from the transcript."""
        state = self._states.get(session_id)
        if state is not None:
            return state
        records = await self._runtime.transcripts.read_all(session_id)
        latest: AutoCompactRecord | None = None
        failures = 0
        for record in records:
            if record.get("kind") == "autocompact-success":
                signature = record.get("boundary_signature")
                occurrence = record.get("boundary_occurrence")
                summary = record.get("summary")
                source = record.get("source")
                if (all(isinstance(value, str) for value in (signature, summary, source))
                        and isinstance(occurrence, int) and occurrence > 0):
                    latest = AutoCompactRecord(
                        signature,
                        occurrence,
                        summary,
                        source,
                    )
                    failures = 0
            elif record.get("kind") == "autocompact-failure":
                failures += 1
        state = AutoCompactState(latest_compaction=latest, consecutive_failures=failures)
        self._states[session_id] = state
        return state

    def _summary_content(self, summary: str) -> Content:
        """Wrap a compaction summary in stable model-visible user content."""
        return Content(
            role="user",
            parts=[Part.from_text(text=AUTOCOMPACT_SUMMARY_PREFIX + summary)],
        )

    def _summary_with_recovery_path(self, summary: str, session_id: str) -> str:
        """Append recovery paths for the full transcript and session memory."""
        return (f"{summary.rstrip()}\n\n"
                "For exact content from before compaction, read the complete transcript: "
                f"{self._runtime.paths.transcript_path(session_id)}\n"
                "Current session memory: "
                f"{self._runtime.paths.session_memory_path(session_id)}")

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
            len(contents) - self._runtime.config.autocompact_keep_recent_contents,
        )
        return self._adjust_start_for_tool_pairing(contents, start)

    def _session_memory_compaction_start(self, boundary_index: int) -> int:
        """Drop everything through the session-memory checkpoint boundary."""
        return boundary_index + 1

    def _apply_record(self, request: "LlmRequest", record: AutoCompactRecord) -> bool:
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
        session_id: str,
    ) -> tuple[str, str] | None:
        """Read session memory and its checkpoint Event for model-free compaction."""
        async with self._runtime.coordination.guard(
                session_id,
                timeout=self._runtime.config.session_memory_wait_timeout_seconds,
        ) as acquired:
            if not acquired:
                return None
            memory = await self._runtime.session_memory.read(session_id)
            if memory is None or memory.strip() == SessionMemoryDocument().to_markdown().strip():
                return None
            records = await self._runtime.transcripts.read_all(session_id)
        for record in reversed(records):
            if record.get("kind") == "session-memory-checkpoint" and isinstance(record.get("last_event_id"), str):
                return memory, record["last_event_id"]
        return None

    def _event_content_signature(
        self,
        records: list[dict[str, Any]],
        event_id: str,
    ) -> tuple[str, int] | None:
        """Recover a boundary signature and occurrence from transcript Events."""
        signatures: list[str] = []
        for record in records:
            if record.get("kind") != "event":
                continue
            raw_content = record.get("event", {}).get("content")
            if not isinstance(raw_content, dict):
                continue
            try:
                signature = content_signature(Content.model_validate(raw_content))
            except Exception:  # noqa: BLE001
                return None
            signatures.append(signature)
            if record.get("event_id") == event_id:
                return signature, signatures.count(signature)
        return None

    def _compact_with_summary(
        self,
        request: "LlmRequest",
        *,
        summary: str,
        boundary_index: int,
        source: str,
        strict_boundary: bool = False,
    ) -> AutoCompactRecord:
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
        return AutoCompactRecord(
            boundary_signature,
            boundary_occurrence,
            summary,
            source,
        )

    def _bounded_history(self, contents: list[Content]) -> str:
        """Bound old history to the configured summary-input character limit."""
        rendered = "\n".join(f"<content>\n{_content_text(content)}\n</content>" for content in contents)
        limit = self._runtime.config.autocompact_summary_input_max_chars
        if len(rendered) <= limit:
            return rendered
        marker = "\n...[middle of old history omitted due to the summary input limit]...\n"
        first_size = max(1, (limit - len(marker)) // 3)
        last_size = max(1, limit - len(marker) - first_size)
        return rendered[:first_size] + marker + rendered[-last_size:]

    async def _legacy_summary(
        self,
        contents: list[Content],
        ctx: "InvocationContext",
    ) -> str:
        """Shrink old history across retries and generate a legacy summary."""
        retries = self._runtime.config.autocompact_summary_retries
        working = list(contents)
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                return await self._summary_generator.generate(
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

    async def _persist_success(
        self,
        session_id: str,
        record: AutoCompactRecord,
        before_chars: int,
        after_chars: int,
        before_tokens: int | None = None,
        after_tokens: int | None = None,
        token_source: str | None = None,
    ) -> None:
        """Persist a successful compaction and reset the circuit-breaker count."""
        await self._runtime.transcripts.append(
            session_id,
            {
                "schema_version": AUTOCOMPACT_SCHEMA_VERSION,
                "kind": "autocompact-success",
                "compaction_id": f"autocompact:{uuid.uuid4().hex}",
                "boundary_signature": record.boundary_signature,
                "boundary_occurrence": record.boundary_occurrence,
                "summary": record.summary,
                "source": record.source,
                "request_chars_before": before_chars,
                "request_chars_after": after_chars,
                "request_tokens_before": before_tokens,
                "request_tokens_after": after_tokens,
                "token_source": token_source,
            },
        )

    async def _persist_failure(
        self,
        session_id: str,
        error: Exception,
        failures: int,
        token_budget: Any | None = None,
    ) -> None:
        """Persist failures so the circuit breaker survives a restart."""
        await self._runtime.transcripts.append(
            session_id,
            {
                "schema_version":
                AUTOCOMPACT_SCHEMA_VERSION,
                "kind":
                "autocompact-failure",
                "attempt_id":
                f"autocompact:{uuid.uuid4().hex}",
                "consecutive_failures":
                failures,
                "error":
                str(error),
                "request_tokens": (token_budget.estimate.tokens
                                   if token_budget is not None and token_budget.token_mode_enabled else None),
                "context_window_tokens": (token_budget.context_window_tokens
                                          if token_budget is not None and token_budget.token_mode_enabled else None),
                "token_source": (token_budget.estimate.source
                                 if token_budget is not None and token_budget.token_mode_enabled else None),
            },
        )

    async def apply(
        self,
        request: "LlmRequest",
        *,
        session_id: str,
        ctx: "InvocationContext",
        force: bool = False,
    ) -> AutoCompactResult:
        """Replay old compaction and compact again when pressure is high."""
        config = self._runtime.config
        tracker = TokenContextTracker(config)
        if not config.enabled or not config.autocompact_enabled:
            request_chars = estimate_request_chars(request)
            return AutoCompactResult(False, False, False, None, request_chars, request_chars, 0)
        await self._runtime.initialize()
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
            blocking_reached = (request_tokens_before >= token_budget_before.blocking_threshold_tokens
                                if token_mode else request_chars_before >= config.autocompact_blocking_chars)
            if state.consecutive_failures >= config.autocompact_max_failures and blocking_reached:
                return AutoCompactResult(
                    False,
                    reapplied,
                    True,
                    None,
                    request_chars_before,
                    request_chars_before,
                    state.consecutive_failures,
                )
            if state.consecutive_failures >= config.autocompact_max_failures:
                return AutoCompactResult(
                    False,
                    reapplied,
                    False,
                    None,
                    request_chars_before,
                    request_chars_before,
                    state.consecutive_failures,
                )
            autocompact_reached = (request_tokens_before >= token_budget_before.autocompact_threshold_tokens
                                   if token_mode else request_chars_before >= config.autocompact_trigger_chars)
            if not force and not autocompact_reached:
                return AutoCompactResult(
                    False,
                    reapplied,
                    False,
                    state.latest_compaction.source if reapplied and state.latest_compaction else None,
                    request_chars_before,
                    request_chars_before,
                    state.consecutive_failures,
                )

            original_contents = [content.model_copy(deep=True) for content in request.contents]
            try:
                compact_record: AutoCompactRecord | None = None
                session_memory = await self._latest_session_memory_record(session_id)
                if session_memory is not None:
                    memory, checkpoint_event_id = session_memory
                    transcript_records = await self._runtime.transcripts.read_all(session_id)
                    boundary = self._event_content_signature(
                        transcript_records,
                        checkpoint_event_id,
                    )
                    if boundary is not None:
                        boundary_signature, boundary_occurrence = boundary
                        boundary_index = self._find_signature_index(
                            request.contents,
                            boundary_signature,
                            boundary_occurrence,
                        )
                        if boundary_index is not None:
                            compact_record = self._compact_with_summary(
                                request,
                                summary=self._summary_with_recovery_path(
                                    memory,
                                    session_id,
                                ),
                                boundary_index=boundary_index,
                                source="session-memory",
                                strict_boundary=True,
                            )
                            target_reached = (tracker.budget(request, ctx).estimate.tokens
                                              <= token_budget_before.warning_threshold_tokens if token_mode else
                                              estimate_request_chars(request) <= config.autocompact_target_chars)
                            if not target_reached:
                                request.contents = [content.model_copy(deep=True) for content in original_contents]
                                compact_record = None

                if compact_record is None:
                    keep_count = min(
                        config.autocompact_keep_recent_contents,
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
                        summary=self._summary_with_recovery_path(
                            summary,
                            session_id,
                        ),
                        boundary_index=boundary_index,
                        source="legacy",
                    )

                request_chars_after = estimate_request_chars(request)
                if request_chars_after >= request_chars_before:
                    raise ValueError("Autocompact did not reduce request size")
                token_budget_after = tracker.budget(request, ctx)
                if token_mode and token_budget_after.estimate.tokens >= request_tokens_before:
                    raise ValueError("Autocompact did not reduce request token estimate")
                await self._persist_success(
                    session_id,
                    compact_record,
                    request_chars_before,
                    request_chars_after,
                    request_tokens_before if token_mode else None,
                    token_budget_after.estimate.tokens if token_mode else None,
                    token_budget_after.estimate.source if token_mode else None,
                )
                state.latest_compaction = compact_record
                state.consecutive_failures = 0
                return AutoCompactResult(
                    True,
                    reapplied,
                    False,
                    compact_record.source,
                    request_chars_before,
                    request_chars_after,
                    0,
                    request_tokens_before=request_tokens_before if token_mode else None,
                    request_tokens_after=(token_budget_after.estimate.tokens if token_mode else None),
                    token_source=token_budget_after.estimate.source if token_mode else None,
                )
            except Exception as exc:  # noqa: BLE001
                request.contents = original_contents
                state.consecutive_failures += 1
                await self._persist_failure(
                    session_id,
                    exc,
                    state.consecutive_failures,
                    token_budget_before,
                )
                blocked = state.consecutive_failures >= config.autocompact_max_failures and blocking_reached
                return AutoCompactResult(
                    False,
                    reapplied,
                    blocked,
                    None,
                    request_chars_before,
                    request_chars_before,
                    state.consecutive_failures,
                    error=str(exc),
                    request_tokens_before=request_tokens_before if token_mode else None,
                    request_tokens_after=request_tokens_before if token_mode else None,
                    token_source=token_budget_before.estimate.source if token_mode else None,
                )


class AutoCompactCallback:
    """Adapt the automatic compressor to before_model_callback."""

    advanced_memory_stage = 40

    def __init__(self, autocompact: AutoCompact) -> None:
        """Store the compressor executed before model requests."""
        self._autocompact = autocompact

    @property
    def autocompact(self) -> AutoCompact:
        """Return the compressor used by this callback."""
        return self._autocompact

    async def __call__(
        self,
        ctx: "InvocationContext",
        request: "LlmRequest",
    ) -> LlmResponse | None:
        """Compact before each request and return a local block after failures."""
        result = await self._autocompact.apply(
            request,
            session_id=ctx.session_id,
            ctx=ctx,
        )
        if not result.blocked:
            TokenContextTracker(self._autocompact.runtime.config).record_request_context(
                request,
                ctx,
            )
            return None
        return LlmResponse(content=Content(
            role="model",
            parts=[Part.from_text(text=AUTOCOMPACT_BLOCKED_MESSAGE)],
        ))


def setup_autocompact(
    agent: "ParentLlmAgent",
    memory_runtime: AdvancedMemoryRuntime,
    summary_generator: LegacySummaryGenerator | None = None,
    *,
    model: Any | None = None,
) -> AutoCompact:
    """Install the automatic compaction callback in pipeline stage order."""
    autocompact = AutoCompact(
        memory_runtime,
        summary_generator,
        model=model,
    )
    callback = AutoCompactCallback(autocompact)
    existing_autocompact = install_staged_callback(
        agent,
        callback,
        callback_type=AutoCompactCallback,
        component_attribute="autocompact",
        memory_runtime=memory_runtime,
        conflict_message="Autocompact is already configured with another runtime",
    )
    return existing_autocompact or autocompact
