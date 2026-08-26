"""Tests for index injection and unified context setup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trpc_agent_sdk.advanced_memory import AutoCompactCallback
from trpc_agent_sdk.advanced_memory import AdvancedMemoryConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import HistorySnipCallback
from trpc_agent_sdk.advanced_memory import LongTermMemoryContext
from trpc_agent_sdk.advanced_memory import LongTermMemoryContextCallback
from trpc_agent_sdk.advanced_memory import MemoryIndexEntry
from trpc_agent_sdk.advanced_memory import MicrocompactCallback
from trpc_agent_sdk.advanced_memory import setup_advanced_memory
from trpc_agent_sdk.advanced_memory import setup_context_management
from trpc_agent_sdk.advanced_memory import ToolResultBudgetCallback
from trpc_agent_sdk.advanced_memory import TranscriptSessionService
from trpc_agent_sdk.advanced_memory._callbacks import install_staged_callback
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions import InMemorySessionService


class FakeSummaryGenerator:
    """Provide a summary generator that does not call a real model."""

    async def generate(self, history: str, ctx) -> str:
        """Return a fixed test summary."""
        del history, ctx
        return "summary"


def _runtime(tmp_path: Path) -> AdvancedMemoryRuntime:
    """Create a test runtime with long-term memory injection enabled."""
    return AdvancedMemoryRuntime.create(AdvancedMemoryConfig(
        enabled=True,
        root_dir=tmp_path,
    ))


def test_staged_callback_rejects_invalid_stage(tmp_path: Path) -> None:
    """Ensure a newly installed callback must declare an integer stage."""
    agent = SimpleNamespace(before_model_callback=None)

    with pytest.raises(TypeError, match="advanced_memory_stage must be an integer"):
        install_staged_callback(
            agent,
            SimpleNamespace(advanced_memory_stage=None),
            callback_type=SimpleNamespace,
            component_attribute="component",
            memory_runtime=_runtime(tmp_path),
            conflict_message="conflict",
        )


def test_staged_callback_treats_invalid_existing_stage_as_zero(tmp_path: Path) -> None:
    """Ensure malformed stages on existing callbacks do not break ordering."""

    class StagedCallback:
        advanced_memory_stage = 10

    existing_callback = SimpleNamespace(advanced_memory_stage=None)
    callback = StagedCallback()
    agent = SimpleNamespace(before_model_callback=existing_callback)

    install_staged_callback(
        agent,
        callback,
        callback_type=StagedCallback,
        component_attribute="component",
        memory_runtime=_runtime(tmp_path),
        conflict_message="conflict",
    )

    assert agent.before_model_callback == [existing_callback, callback]


async def test_long_term_memory_index_is_injected_once(tmp_path: Path) -> None:
    """Ensure the index, paths, and on-demand read guidance are injected."""
    runtime = _runtime(tmp_path)
    await runtime.long_term_memory.write_index(
        [MemoryIndexEntry(
            name="项目约定",
            filename="project.md",
            summary="保存项目代码规范",
        )])
    request = LlmRequest(model="test-model")
    context = LongTermMemoryContext(runtime)

    first = await context.apply(request)
    second = await context.apply(request)

    instruction = str(request.config.system_instruction)
    assert first is True
    assert second is False
    assert instruction.count("<advanced-memory-index>") == 1
    assert "- [项目约定]（project.md）:保存项目代码规范" in instruction
    assert str(runtime.paths.memory_index_path) in instruction
    assert 'do not wait for the user to say "remember this"' in instruction
    assert "For corrections, replacements, or important additions" in instruction
    assert "secrets, credentials, tokens, and other sensitive data" in instruction


async def test_unified_setup_installs_complete_pipeline_in_order(tmp_path: Path) -> None:
    """Ensure unified setup installs the five components in order."""
    runtime = _runtime(tmp_path)
    agent = SimpleNamespace(before_model_callback=None)

    components = setup_context_management(
        agent,
        runtime,
        FakeSummaryGenerator(),
    )

    assert components.long_term_memory.runtime is runtime
    assert isinstance(agent.before_model_callback[0], LongTermMemoryContextCallback)
    assert isinstance(agent.before_model_callback[1], ToolResultBudgetCallback)
    assert isinstance(agent.before_model_callback[2], HistorySnipCallback)
    assert isinstance(agent.before_model_callback[3], MicrocompactCallback)
    assert isinstance(agent.before_model_callback[4], AutoCompactCallback)


async def test_full_setup_wraps_session_service_and_is_idempotent(tmp_path: Path, ) -> None:
    """Ensure unified setup assembles transcript, session memory, and callbacks."""
    runtime = _runtime(tmp_path)
    agent = SimpleNamespace(before_model_callback=None, tools=[])

    first = setup_advanced_memory(
        agent,
        InMemorySessionService(),
        runtime,
        FakeSummaryGenerator(),
    )
    second = setup_advanced_memory(
        agent,
        first.session_service,
        runtime,
        FakeSummaryGenerator(),
    )

    assert isinstance(first.session_service, TranscriptSessionService)
    assert first.session_memory_extractor.runtime is runtime
    assert first.session_service.session_memory_extractor is first.session_memory_extractor
    assert second.session_service is first.session_service
    assert second.session_memory_extractor is first.session_memory_extractor
    assert second.long_term_memory_tools is first.long_term_memory_tools
    assert len(agent.before_model_callback) == 5
    tool_names = {tool.name for tool in agent.tools}
    assert tool_names == {
        "save_memory",
        "read_memory",
        "list_memory_index",
    }


async def test_disabled_runtime_does_not_modify_system_instruction(tmp_path: Path) -> None:
    """Ensure disabled runtime does not inject long-term memory."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryConfig(enabled=False, root_dir=tmp_path))
    request = LlmRequest(model="test-model")

    applied = await LongTermMemoryContext(runtime).apply(request)

    assert applied is False
    assert request.config is None
