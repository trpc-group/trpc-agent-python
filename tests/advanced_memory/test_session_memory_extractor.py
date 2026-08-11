"""Unit tests for full-context session-memory extraction and isolation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import ForkedSessionMemoryGenerator
from trpc_agent_sdk.advanced_memory import SessionMemoryExtractionInput
from trpc_agent_sdk.advanced_memory import SessionMemoryDocument
from trpc_agent_sdk.advanced_memory import SessionMemoryExtractor
from trpc_agent_sdk.advanced_memory import TranscriptSessionService
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Part


class FakeSessionMemoryGenerator:
    """Record extraction input and return a deterministic document."""

    def __init__(self, *, fail: bool = False) -> None:
        """Initialize call tracking and the optional failure switch."""
        self.inputs = []
        self.fail = fail

    async def generate(self, extraction_input, ctx) -> SessionMemoryDocument:
        """Generate a fixed test document from the last Event ID."""
        self.inputs.append(extraction_input)
        if self.fail:
            raise RuntimeError("generator failed")
        return SessionMemoryDocument(
            session_title="增量抽取测试",
            current_state=f"已处理到 {extraction_input.last_event_id}",
            task_specification="验证 session memory 增量更新。",
            worklog=f"- {extraction_input.first_event_id} -> {extraction_input.last_event_id}",
        )


class EmptySessionMemoryGenerator:
    """Simulate an invalid extractor returning ten empty sections."""

    async def generate(self, extraction_input, ctx) -> SessionMemoryDocument:
        """Ignore input and return a complete empty template."""
        del extraction_input, ctx
        return SessionMemoryDocument()


class StructuredMemoryModel(LLMModel):
    """Return an isolated Runner model with fixed Markdown memory."""

    def __init__(self, *, empty: bool = False) -> None:
        """Initialize the test model and store received requests."""
        super().__init__(model_name="session-memory-test-model")
        self.requests = []
        self.empty = empty

    @classmethod
    def supported_models(cls):
        """Declare the names supported by the test model."""
        return [r"session-memory-test-model"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        """Record a request and return parser-compatible Markdown."""
        self.requests.append(request)
        payload = ("# Session Title\n\n" if self.empty else SessionMemoryDocument(
            session_title="隔离 Runner",
            current_state="子 Agent 已完成。",
        ).to_markdown())
        yield LlmResponse(content=Content(
            role="model",
            parts=[Part.from_text(text=payload)],
        ))

    def validate_request(self, request):
        """Allow all model requests in tests."""
        return None


def _runtime(
    tmp_path: Path,
    *,
    initial_chars: int = 1,
    update_chars: int = 1,
    prompt_max_chars: int = 10_000,
    section_max_chars: int = 8_000,
) -> AdvancedMemoryRuntime:
    """Create an isolated runtime with small extraction limits."""
    return AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            session_memory_initial_chars=initial_chars,
            session_memory_update_chars=update_chars,
            session_memory_prompt_max_chars=prompt_max_chars,
            session_memory_section_max_chars=section_max_chars,
        ))


def _event(event_id: str, text: str) -> Event:
    """Create a non-streaming Event for a transcript."""
    return Event(
        id=event_id,
        invocation_id="invocation-1",
        author="agent",
        content=Content(parts=[Part.from_text(text=text)]),
    )


async def _service_and_session(runtime: AdvancedMemoryRuntime):
    """Create a test SessionService and session with automatic transcripts."""
    service = TranscriptSessionService(InMemorySessionService(), runtime)
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="demo-session",
    )
    return service, session


def _ctx(session):
    """Create the minimal InvocationContext stand-in for generator tests."""
    return SimpleNamespace(session=session, agent=SimpleNamespace(model="fake-model"))


async def test_first_extraction_writes_document_and_checkpoint(tmp_path: Path) -> None:
    """Ensure the first threshold hit generates a document and records a boundary."""
    runtime = _runtime(tmp_path)
    service, session = await _service_and_session(runtime)
    await service.append_event(session, _event("event-1", "分析项目结构"))
    await service.append_event(session, _event("event-2", "完成第一阶段"))
    generator = FakeSessionMemoryGenerator()

    result = await SessionMemoryExtractor(runtime, generator).extract_if_needed(
        session,
        _ctx(session),
    )

    memory = await runtime.session_memory.read(session.id)
    records = await runtime.transcripts.read_all(session.id)
    checkpoints = [record for record in records if record["kind"] == "session-memory-checkpoint"]
    assert result.extracted is True
    assert result.processed_events == 2
    assert "# Session Title\n_A short and distinctive" in memory
    assert "\n\n增量抽取测试" in memory
    assert "# Learnings\n_What has worked well?" in memory
    assert checkpoints[-1]["last_event_id"] == "event-2"


async def test_token_threshold_triggers_extraction_before_character_threshold(tmp_path: Path) -> None:
    """Ensure session memory uses token thresholds when configured."""
    runtime = AdvancedMemoryRuntime.create(
        AdvancedMemoryConfig(
            enabled=True,
            root_dir=tmp_path,
            session_memory_initial_chars=100_000,
            session_memory_update_chars=100_000,
            session_memory_initial_tokens=10,
            session_memory_update_tokens=10,
            session_memory_tool_calls_between_updates=1,
            model_context_window_tokens=1_000,
            max_output_tokens=100,
            session_memory_request_overhead_tokens=50,
        ))
    service, session = await _service_and_session(runtime)
    await service.append_event(session, _event("event-1", "x" * 200))

    result = await SessionMemoryExtractor(
        runtime,
        FakeSessionMemoryGenerator(),
    ).extract_if_needed(session, _ctx(session))

    assert result.extracted is True


async def test_next_extraction_uses_full_context_after_checkpoint(tmp_path: Path) -> None:
    """Ensure each update receives the full visible context and a checkpoint delta."""
    runtime = _runtime(tmp_path)
    service, session = await _service_and_session(runtime)
    generator = FakeSessionMemoryGenerator()
    extractor = SessionMemoryExtractor(runtime, generator)
    await service.append_event(session, _event("event-1", "first"))
    await extractor.extract_if_needed(session, _ctx(session))
    await service.append_event(session, _event("event-2", "second"))

    result = await extractor.extract_if_needed(session, _ctx(session))

    assert result.extracted is True
    assert result.processed_events == 1
    assert generator.inputs[-1].first_event_id == "event-2"
    assert "已处理到 event-1" in generator.inputs[-1].current_memory
    assert "first" in generator.inputs[-1].context_messages
    assert "second" in generator.inputs[-1].context_messages
    assert generator.inputs[-1].new_events == ""


async def test_context_messages_keep_latest_content_and_remove_metadata(tmp_path: Path, ) -> None:
    """Ensure full visible Content excludes thoughts and Event metadata."""
    runtime = _runtime(tmp_path, prompt_max_chars=5_000)
    service, session = await _service_and_session(runtime)
    await service.append_event(session, _event("event-old", "old-" + "x" * 300))
    latest = Event(
        id="event-latest",
        invocation_id="invocation-secret",
        author="agent-secret",
        content=Content(
            role="model",
            parts=[
                Part(text="hidden reasoning", thought=True),
                Part.from_text(text="latest visible answer"),
            ],
        ),
    )
    await service.append_event(session, latest)
    generator = FakeSessionMemoryGenerator()

    result = await SessionMemoryExtractor(runtime, generator).extract_if_needed(
        session,
        _ctx(session),
        force=True,
    )

    context_messages = generator.inputs[0].context_messages
    all_context = context_messages + generator.inputs[0].new_events
    assert result.extracted is True
    assert "latest visible answer" in context_messages
    assert "old-" in context_messages
    assert "hidden reasoning" not in all_context
    assert "event-latest" not in all_context
    assert "invocation-secret" not in all_context
    assert "agent-secret" not in all_context


async def test_full_context_is_sent_without_checkpoint_duplication(tmp_path: Path, ) -> None:
    """Ensure the session memory Agent receives the complete visible context."""
    runtime = _runtime(tmp_path, prompt_max_chars=20_000)
    service, session = await _service_and_session(runtime)
    await service.append_event(session, _event("event-1", "old-context-" + "x" * 1_000))
    await service.append_event(session, _event("event-2", "recent-context-" + "y" * 1_000))
    await service.append_event(session, _event("event-3", "latest-context-" + "z" * 1_000))
    generator = FakeSessionMemoryGenerator()

    result = await SessionMemoryExtractor(runtime, generator).extract_if_needed(
        session,
        _ctx(session),
        force=True,
    )

    extraction_input = generator.inputs[0]
    assert result.extracted is True
    assert result.processed_events == 3
    assert "latest-context-" in extraction_input.context_messages
    assert "old-context-" in extraction_input.context_messages
    assert "recent-context-" in extraction_input.context_messages
    assert extraction_input.new_events == ""


async def test_full_context_over_budget_does_not_advance_checkpoint(tmp_path: Path, ) -> None:
    """Process the largest safe event prefix instead of stalling forever."""
    runtime = _runtime(tmp_path, prompt_max_chars=3_000)
    service, session = await _service_and_session(runtime)
    for index in range(3):
        await service.append_event(session, _event(f"event-{index}", "x" * 1_000))
    result = await SessionMemoryExtractor(runtime, FakeSessionMemoryGenerator()).extract_if_needed(
        session,
        _ctx(session),
        force=True,
    )

    assert result.extracted is True
    assert result.processed_events == 3


async def test_compacted_context_can_still_process_transcript_delta(tmp_path: Path, ) -> None:
    """Ensure events omitted by compaction are supplied from the transcript delta."""
    runtime = _runtime(tmp_path, prompt_max_chars=10_000)
    service, session = await _service_and_session(runtime)
    generator = FakeSessionMemoryGenerator()
    extractor = SessionMemoryExtractor(runtime, generator)

    await service.append_event(session, _event("event-1", "old context"))
    await extractor.extract_if_needed(session, _ctx(session))
    await service.append_event(session, _event("event-2", "new context"))

    compacted_ctx = SimpleNamespace(
        session=session,
        agent=SimpleNamespace(model="fake-model"),
        override_messages=[
            Content(parts=[Part.from_text(text="compact summary")]),
        ],
    )
    result = await extractor.extract_if_needed(session, compacted_ctx)

    assert result.extracted is True
    assert result.last_event_id == "event-2"
    assert "new context" in generator.inputs[-1].context_messages
    assert generator.inputs[-1].new_events == ""
    assert "old context" not in generator.inputs[-1].context_messages
    assert generator.inputs[-1].context_messages.index("new context") < generator.inputs[-1].context_messages.index(
        "compact summary")


async def test_missing_checkpoint_recovers_only_newer_timestamped_events(tmp_path: Path, ) -> None:
    """Ensure a missing checkpoint Event does not re-extract the transcript."""
    runtime = _runtime(tmp_path)
    service, session = await _service_and_session(runtime)
    await service.append_event(session, _event("event-old", "旧内容"))
    await runtime.transcripts.append(
        session.id,
        {
            "kind": "session-memory-checkpoint",
            "checkpoint_id": "session-memory:event-missing",
            "first_event_id": "event-missing",
            "last_event_id": "event-missing",
        },
    )
    await service.append_event(session, _event("event-new", "新内容"))
    generator = FakeSessionMemoryGenerator()

    result = await SessionMemoryExtractor(
        runtime,
        generator,
    ).extract_if_needed(
        session,
        _ctx(session),
        force=True,
    )

    assert result.extracted is True
    assert result.processed_events == 1
    assert generator.inputs[0].first_event_id == "event-new"
    assert generator.inputs[0].last_event_id == "event-new"


async def test_no_new_events_does_not_call_generator(tmp_path: Path) -> None:
    """Ensure no checkpoint increment means no repeated sub-agent call."""
    runtime = _runtime(tmp_path)
    service, session = await _service_and_session(runtime)
    generator = FakeSessionMemoryGenerator()
    extractor = SessionMemoryExtractor(runtime, generator)
    await service.append_event(session, _event("event-1", "first"))
    await extractor.extract_if_needed(session, _ctx(session))

    result = await extractor.extract_if_needed(session, _ctx(session))

    assert result.reason == "no-new-events"
    assert len(generator.inputs) == 1


async def test_failure_does_not_advance_checkpoint_and_can_retry(tmp_path: Path) -> None:
    """Ensure extraction failure leaves the increment for the next attempt."""
    runtime = _runtime(tmp_path)
    service, session = await _service_and_session(runtime)
    await service.append_event(session, _event("event-1", "first"))
    failing_generator = FakeSessionMemoryGenerator(fail=True)

    failed = await SessionMemoryExtractor(runtime, failing_generator).extract_if_needed(
        session,
        _ctx(session),
    )
    successful_generator = FakeSessionMemoryGenerator()
    succeeded = await SessionMemoryExtractor(runtime, successful_generator).extract_if_needed(
        session,
        _ctx(session),
    )

    assert failed.reason == "extraction-failed"
    assert succeeded.extracted is True
    assert successful_generator.inputs[0].first_event_id == "event-1"


async def test_empty_document_does_not_overwrite_or_advance_checkpoint(tmp_path: Path, ) -> None:
    """Ensure all-empty output fails and preserves old session memory."""
    runtime = _runtime(tmp_path)
    service, session = await _service_and_session(runtime)
    old_document = SessionMemoryDocument(
        session_title="已有记忆",
        current_state="等待新事件。",
    )
    await runtime.session_memory.write(session.id, old_document)
    await service.append_event(session, _event("event-1", "first"))

    result = await SessionMemoryExtractor(
        runtime,
        EmptySessionMemoryGenerator(),
    ).extract_if_needed(
        session,
        _ctx(session),
        force=True,
    )

    records = await runtime.transcripts.read_all(session.id)
    assert result.reason == "extraction-failed"
    assert await runtime.session_memory.read(session.id) == old_document.to_markdown()
    assert not any(record.get("kind") == "session-memory-checkpoint" for record in records)


async def test_force_bypasses_initial_threshold(tmp_path: Path) -> None:
    """Ensure forced extraction bypasses the initial character threshold."""
    runtime = _runtime(tmp_path, initial_chars=100_000, update_chars=100_000)
    service, session = await _service_and_session(runtime)
    await service.append_event(session, _event("event-1", "small"))

    result = await SessionMemoryExtractor(
        runtime,
        FakeSessionMemoryGenerator(),
    ).extract_if_needed(
        session,
        _ctx(session),
        force=True,
    )

    assert result.extracted is True
    assert result.reason == "forced"


async def test_pending_tool_call_does_not_create_a_checkpoint_boundary(tmp_path: Path) -> None:
    """Ensure a pending tool call cannot become a compaction boundary."""
    runtime = _runtime(tmp_path)
    service, session = await _service_and_session(runtime)
    tool_event = Event(
        id="event-tool",
        invocation_id="invocation-1",
        author="agent",
        content=Content(
            role="model",
            parts=[Part(function_call=FunctionCall(
                id="call-1",
                name="Read",
                args={"file_path": "demo.py"},
            ))],
        ),
    )
    await service.append_event(session, tool_event)

    result = await SessionMemoryExtractor(
        runtime,
        FakeSessionMemoryGenerator(),
    ).extract_if_needed(
        session,
        _ctx(session),
    )

    assert result.reason == "unsafe-boundary"


async def test_session_service_runs_extractor_after_old_summary(tmp_path: Path) -> None:
    """Ensure the Runner post-turn extension automatically triggers extraction."""
    runtime = _runtime(tmp_path)
    generator = FakeSessionMemoryGenerator()
    extractor = SessionMemoryExtractor(runtime, generator)
    service = TranscriptSessionService(
        InMemorySessionService(),
        runtime,
        session_memory_extractor=extractor,
    )
    session = await service.create_session(
        app_name="demo-app",
        user_id="demo-user",
        session_id="demo-session",
    )
    await service.append_event(session, _event("event-1", "post turn"))

    await service.create_session_summary(session, ctx=_ctx(session))

    assert len(generator.inputs) == 1
    assert await runtime.session_memory.read(session.id) is not None


async def test_forked_generator_uses_isolated_runner_and_returns_memory() -> None:
    """Ensure the default generator makes one isolated Markdown Runner call."""
    model = StructuredMemoryModel()
    generator = ForkedSessionMemoryGenerator(model)
    extraction_input = SessionMemoryExtractionInput(
        current_memory=SessionMemoryDocument().to_markdown(),
        first_event_id="event-1",
        last_event_id="event-1",
        context_messages="<message>surrounding context</message>",
    )
    ctx = SimpleNamespace(
        app_name="demo-app",
        agent=SimpleNamespace(model=model),
    )

    document = await generator.generate(extraction_input, ctx)

    assert document.session_title == "隔离 Runner"
    assert document.current_state == "子 Agent 已完成。"
    assert len(model.requests) == 1
    assert "surrounding context" in model.requests[0].contents[-1].parts[0].text


async def test_forked_generator_rejects_empty_markdown_output() -> None:
    """Ensure an empty Markdown response does not create empty session memory."""
    model = StructuredMemoryModel(empty=True)
    generator = ForkedSessionMemoryGenerator(model)
    extraction_input = SessionMemoryExtractionInput(
        current_memory=SessionMemoryDocument().to_markdown(),
        first_event_id="event-1",
        last_event_id="event-1",
        context_messages="<message>new work</message>",
    )
    ctx = SimpleNamespace(
        app_name="demo-app",
        agent=SimpleNamespace(model=model),
    )

    with pytest.raises(ValueError):
        await generator.generate(extraction_input, ctx)
