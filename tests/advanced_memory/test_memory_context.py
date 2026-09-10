"""Tests for index injection and unified context setup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from trpc_agent_sdk.advanced_memory import AdvancedMemoryServiceConfig
from trpc_agent_sdk.advanced_memory import AdvancedMemoryRuntime
from trpc_agent_sdk.advanced_memory import LongTermMemoryContext
from trpc_agent_sdk.advanced_memory import LongTermMemoryContextCallback
from trpc_agent_sdk.advanced_memory import MemoryIndexEntry
from trpc_agent_sdk.memory import AdvancedMemoryService
from trpc_agent_sdk.models import LlmRequest
from trpc_agent_sdk.sessions.compact._callbacks import install_staged_callback
from trpc_agent_sdk.sessions import InMemorySessionService


def _runtime(tmp_path: Path) -> AdvancedMemoryRuntime:
    """Create a test runtime with long-term memory injection enabled."""
    return AdvancedMemoryRuntime.create(AdvancedMemoryServiceConfig(
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


async def test_custom_memory_focus_is_injected_into_system_instruction(tmp_path: Path) -> None:
    """Ensure applications can prioritize a custom long-term memory focus."""
    runtime = AdvancedMemoryRuntime.create(
        AdvancedMemoryServiceConfig(
            enabled=True,
            root_dir=tmp_path,
            memory_focus_instruction="重点记住用户长期稳定的兴趣爱好。",
        ))
    request = LlmRequest(model="test-model")

    applied = await LongTermMemoryContext(runtime).apply(request)

    instruction = str(request.config.system_instruction)
    assert applied is True
    assert "## Custom memory focus" in instruction
    assert "重点记住用户长期稳定的兴趣爱好。" in instruction


async def test_memory_service_does_not_install_session_compression(tmp_path: Path, ) -> None:
    """Ensure the MemoryService leaves the supplied SessionService unchanged."""
    runtime = _runtime(tmp_path)
    memory_service = AdvancedMemoryService(runtime=runtime)
    session_service = InMemorySessionService()
    agent = SimpleNamespace(before_model_callback=None, tools=[])

    bound = memory_service.bind(agent, session_service)

    assert bound is session_service
    assert len(agent.before_model_callback) == 1
    assert isinstance(
        agent.before_model_callback[0],
        LongTermMemoryContextCallback,
    )
    tool_names = {tool.name for tool in agent.tools}
    assert tool_names == {
        "save_memory",
        "read_memory",
        "list_memory_index",
    }
    await session_service.close()
    await memory_service.close()


async def test_disabled_runtime_does_not_modify_system_instruction(tmp_path: Path) -> None:
    """Ensure disabled runtime does not inject long-term memory."""
    runtime = AdvancedMemoryRuntime.create(AdvancedMemoryServiceConfig(enabled=False, root_dir=tmp_path))
    request = LlmRequest(model="test-model")

    applied = await LongTermMemoryContext(runtime).apply(request)

    assert applied is False
    assert request.config is None
