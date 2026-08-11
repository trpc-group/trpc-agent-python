"""Unit tests for automatic compaction, replay, and circuit breaking."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from trpc_agent_sdk.advanced_memory import AutoCompact
from trpc_agent_sdk.advanced_memory import AutoCompactCallback
from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import HistorySnipCallback
from trpc_agent_sdk.advanced_memory import MicrocompactCallback
from trpc_agent_sdk.advanced_memory import SessionMemoryDocument
from trpc_agent_sdk.advanced_memory import setup_autocompact
from trpc_agent_sdk.advanced_memory import setup_history_snip
from trpc_agent_sdk.advanced_memory import setup_microcompact
from trpc_agent_sdk.advanced_memory import setup_tool_result_budget
from trpc_agent_sdk.advanced_memory import ToolResultBudgetCallback
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part


class FakeSummaryGenerator:
    """Return a fixed summary or fail according to configuration."""

    def __init__(self, *, fail: bool = False) -> None:
        """Initialize call tracking and the failure switch."""
        self.fail = fail
        self.histories: list[str] = []

    async def generate(self, history: str, ctx) -> str:
        """Record history and return a short summary."""
        self.histories.append(history)
        if self.fail:
            raise RuntimeError("summary failed")
        return "## 压缩摘要\n\n保留用户目标、关键文件和当前状态。"


def _runtime(
    tmp_path: Path,
    *,
    enabled: bool = True,
    trigger: int = 4_000,
    target: int = 3_000,
    blocking: int = 5_000,
    keep_recent: int = 2,
    max_failures: int = 3,
) -> AdvancedMemoryRuntime:
    """Create an isolated runtime with small automatic-compaction limits."""
    return AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=enabled,
            root_dir=tmp_path,
            autocompact_trigger_chars=trigger,
            autocompact_target_chars=target,
            autocompact_blocking_chars=blocking,
            autocompact_keep_recent_contents=keep_recent,
            autocompact_max_failures=max_failures,
            autocompact_summary_input_max_chars=10_000,
            autocompact_summary_retries=2,
        ))


def _request(count: int, *, text_size: int = 800) -> LlmRequest:
    """Create a model request with multiple text Contents."""
    return LlmRequest(
        model="test-model",
        contents=[
            Content(
                role="user" if index % 2 == 0 else "model",
                parts=[Part.from_text(text=f"message-{index}-" + chr(97 + index) * text_size)],
            ) for index in range(count)
        ],
    )


def _ctx(session_id: str = "session-a"):
    """Create the minimal context stand-in required by AutoCompact."""
    return SimpleNamespace(
        session_id=session_id,
        app_name="demo-app",
        agent=SimpleNamespace(model="fake-model"),
    )


async def test_legacy_compact_replaces_old_prefix_and_keeps_recent(tmp_path: Path) -> None:
    """Ensure missing session memory invokes the summary generator."""
    runtime = _runtime(tmp_path)
    generator = FakeSummaryGenerator()
    request = _request(5)

    result = await AutoCompact(runtime, generator).apply(
        request,
        session_id="session-a",
        ctx=_ctx(),
        force=True,
    )

    assert result.compacted is True
    assert result.source == "legacy"
    assert result.request_chars_after < result.request_chars_before
    assert len(request.contents) == 3
    assert "This session is being continued" in request.contents[0].parts[0].text
    assert "message-3-" in request.contents[1].parts[0].text
    assert len(generator.histories) == 1


async def test_token_budget_triggers_autocompact_and_records_diagnostics(tmp_path: Path) -> None:
    """Ensure token thresholds replace character thresholds and persist diagnostics."""
    runtime = AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            autocompact_trigger_chars=100_000,
            autocompact_target_chars=50_000,
            autocompact_blocking_chars=120_000,
            autocompact_keep_recent_contents=2,
            autocompact_summary_input_max_chars=10_000,
            model_context_window_tokens=1_100,
            max_output_tokens=100,
        ))
    result = await AutoCompact(runtime, FakeSummaryGenerator()).apply(
        _request(5),
        session_id="session-a",
        ctx=_ctx(),
    )

    assert result.compacted
    assert result.request_tokens_before is not None
    assert result.request_tokens_after is not None
    assert result.request_tokens_after < result.request_tokens_before
    records = await runtime.transcripts.read_all("session-a")
    assert records[-1]["request_tokens_before"] == result.request_tokens_before


async def test_session_memory_compact_avoids_summary_model_call(tmp_path: Path) -> None:
    """Ensure available session memory takes priority over legacy summaries."""
    runtime = _runtime(
        tmp_path,
        trigger=8_000,
        target=7_000,
        blocking=9_000,
    )
    service = InMemorySessionService()
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="session-a",
    )
    request = _request(5)
    parent_event_id = None
    for index, content in enumerate(request.contents):
        event = Event(
            id=f"event-{index}",
            invocation_id="invocation-1",
            author="agent",
            content=content.model_copy(deep=True),
        )
        await runtime.transcripts.append(
            session.id,
            {
                "schema_version": 1,
                "kind": "event",
                "event_id": event.id,
                "parent_event_id": parent_event_id,
                "session": {
                    "id": session.id
                },
                "event": event.model_dump(mode="json", by_alias=True, exclude_none=True),
            },
        )
        parent_event_id = event.id
    await runtime.session_memory.write(
        session.id,
        SessionMemoryDocument(
            session_title="已有会话记忆",
            current_state="正在继续实现自动压缩。",
        ),
    )
    await runtime.transcripts.append(
        session.id,
        {
            "kind": "session-memory-checkpoint",
            "checkpoint_id": "session-memory:event-2",
            "first_event_id": "event-0",
            "last_event_id": "event-2",
        },
    )
    generator = FakeSummaryGenerator()

    result = await AutoCompact(runtime, generator).apply(
        request,
        session_id=session.id,
        ctx=_ctx(session.id),
        force=True,
    )

    assert result.compacted is True
    assert result.source == "session-memory"
    assert generator.histories == []
    assert "已有会话记忆" in request.contents[0].parts[0].text
    assert "message-3-" in request.contents[1].parts[0].text


async def test_session_memory_compact_drops_all_contents_through_checkpoint(tmp_path: Path, ) -> None:
    """Ensure session-memory compaction does not retain pre-checkpoint contents."""
    runtime = _runtime(
        tmp_path,
        trigger=8_000,
        target=7_000,
        blocking=9_000,
    )
    service = InMemorySessionService()
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="session-a",
    )
    request = _request(5)
    parent_event_id = None
    for index, content in enumerate(request.contents):
        event = Event(
            id=f"event-{index}",
            invocation_id="invocation-1",
            author="agent",
            content=content.model_copy(deep=True),
        )
        await runtime.transcripts.append(
            session.id,
            {
                "schema_version": 1,
                "kind": "event",
                "event_id": event.id,
                "parent_event_id": parent_event_id,
                "session": {
                    "id": session.id
                },
                "event": event.model_dump(mode="json", by_alias=True, exclude_none=True),
            },
        )
        parent_event_id = event.id
    await runtime.session_memory.write(
        session.id,
        SessionMemoryDocument(
            session_title="已有会话记忆",
            current_state="已总结到最后一个 event。",
        ),
    )
    await runtime.transcripts.append(
        session.id,
        {
            "kind": "session-memory-checkpoint",
            "checkpoint_id": "session-memory:event-4",
            "first_event_id": "event-0",
            "last_event_id": "event-4",
        },
    )

    result = await AutoCompact(runtime, FakeSummaryGenerator()).apply(
        request,
        session_id=session.id,
        ctx=_ctx(session.id),
        force=True,
    )

    assert result.compacted is True
    assert result.source == "session-memory"
    assert len(request.contents) == 1
    assert "已有会话记忆" in request.contents[0].parts[0].text


async def test_successful_compaction_is_reapplied_after_restart(tmp_path: Path) -> None:
    """Ensure restart restores and replays the same compaction boundary."""
    first_runtime = _runtime(tmp_path, trigger=20_000, target=10_000, blocking=30_000)
    first_request = _request(5)
    first = await AutoCompact(first_runtime, FakeSummaryGenerator()).apply(
        first_request,
        session_id="session-a",
        ctx=_ctx(),
        force=True,
    )
    first_payload = [content.model_dump(exclude_none=True) for content in first_request.contents]

    second_runtime = _runtime(tmp_path, trigger=20_000, target=10_000, blocking=30_000)
    second_request = _request(5)
    second = await AutoCompact(second_runtime, FakeSummaryGenerator()).apply(
        second_request,
        session_id="session-a",
        ctx=_ctx(),
    )

    assert first.compacted is True
    assert second.compacted is False
    assert second.reapplied is True
    assert [content.model_dump(exclude_none=True) for content in second_request.contents] == first_payload


async def test_reapplied_boundary_preserves_all_new_unsummarized_contents(tmp_path: Path) -> None:
    """Ensure replay does not discard new history beyond recent contents."""
    runtime = _runtime(tmp_path, trigger=50_000, target=20_000, blocking=60_000)
    initial_request = _request(5, text_size=300)
    await AutoCompact(runtime, FakeSummaryGenerator()).apply(
        initial_request,
        session_id="session-a",
        ctx=_ctx(),
        force=True,
    )
    expanded_request = _request(9, text_size=300)

    result = await AutoCompact(runtime, FakeSummaryGenerator()).apply(
        expanded_request,
        session_id="session-a",
        ctx=_ctx(),
    )

    visible_text = "\n".join(part.text or "" for content in expanded_request.contents for part in content.parts or [])
    assert result.reapplied is True
    for index in range(3, 9):
        assert f"message-{index}-" in visible_text


async def test_reapplied_boundary_uses_signature_occurrence_not_last_match(tmp_path: Path, ) -> None:
    """Ensure duplicate Content signatures do not skip unsummarized messages."""
    runtime = _runtime(tmp_path)
    duplicate = "duplicate-" + "d" * 500
    original_contents = [
        Content(role="user", parts=[Part.from_text(text=duplicate)]),
        Content(role="model", parts=[Part.from_text(text="middle-" + "m" * 500)]),
        Content(role="user", parts=[Part.from_text(text=duplicate)]),
        Content(role="user", parts=[Part.from_text(text=duplicate)]),
        Content(role="model", parts=[Part.from_text(text="last-" + "l" * 500)]),
    ]
    await AutoCompact(runtime, FakeSummaryGenerator()).apply(
        LlmRequest(model="test-model", contents=original_contents),
        session_id="session-a",
        ctx=_ctx(),
        force=True,
    )
    second_request = LlmRequest(
        model="test-model",
        contents=[
            *[content.model_copy(deep=True) for content in original_contents],
            Content(role="user", parts=[Part.from_text(text="new-message")]),
        ],
    )

    result = await AutoCompact(
        _runtime(tmp_path),
        FakeSummaryGenerator(),
    ).apply(
        second_request,
        session_id="session-a",
        ctx=_ctx(),
    )

    assert result.reapplied is True
    assert len(second_request.contents) == 4
    assert second_request.contents[1].parts[0].text == duplicate


async def test_failures_retry_internally_then_trip_circuit_breaker(tmp_path: Path) -> None:
    """Ensure failures persist and trigger blocking at the hard limit."""
    runtime = _runtime(
        tmp_path,
        trigger=2_000,
        target=1_000,
        blocking=3_000,
        max_failures=3,
    )
    generator = FakeSummaryGenerator(fail=True)
    compact = AutoCompact(runtime, generator)
    results = []
    for _ in range(3):
        results.append(await compact.apply(
            _request(4, text_size=1_000),
            session_id="session-a",
            ctx=_ctx(),
            force=True,
        ))

    assert [result.consecutive_failures for result in results] == [1, 2, 3]
    assert results[-1].blocked is True
    assert len(generator.histories) == 6
    records = await runtime.transcripts.read_all("session-a")
    assert len([record for record in records if record["kind"] == "autocompact-failure"]) == 3


async def test_circuit_breaker_skips_further_summary_calls_below_hard_limit(tmp_path: Path) -> None:
    """Ensure the circuit breaker avoids summary calls below the hard limit."""
    runtime = _runtime(
        tmp_path,
        trigger=2_000,
        target=1_000,
        blocking=10_000,
        max_failures=1,
    )
    generator = FakeSummaryGenerator(fail=True)
    compact = AutoCompact(runtime, generator)
    await compact.apply(
        _request(4, text_size=800),
        session_id="session-a",
        ctx=_ctx(),
        force=True,
    )
    calls_after_failure = len(generator.histories)

    result = await compact.apply(
        _request(4, text_size=800),
        session_id="session-a",
        ctx=_ctx(),
        force=True,
    )

    assert result.blocked is False
    assert result.consecutive_failures == 1
    assert len(generator.histories) == calls_after_failure


async def test_disabled_autocompact_does_not_copy_request(tmp_path: Path) -> None:
    """Ensure disabled mode preserves requests and disk state."""
    runtime = _runtime(tmp_path, enabled=False)
    request = _request(5)
    original_content = request.contents[0]

    result = await AutoCompact(runtime, FakeSummaryGenerator()).apply(
        request,
        session_id="session-a",
        ctx=_ctx(),
        force=True,
    )

    assert result.compacted is False
    assert request.contents[0] is original_content
    assert not (tmp_path / "SESSION").exists()


def test_setup_orders_full_context_pipeline(tmp_path: Path) -> None:
    """Ensure any setup order yields the expected callback pipeline."""
    runtime = _runtime(tmp_path)
    agent = SimpleNamespace(before_model_callback=None)

    setup_autocompact(agent, runtime, FakeSummaryGenerator())
    setup_microcompact(agent, runtime)
    setup_history_snip(agent, runtime)
    setup_tool_result_budget(agent, runtime)

    assert isinstance(agent.before_model_callback[0], ToolResultBudgetCallback)
    assert isinstance(agent.before_model_callback[1], HistorySnipCallback)
    assert isinstance(agent.before_model_callback[2], MicrocompactCallback)
    assert isinstance(agent.before_model_callback[3], AutoCompactCallback)
