# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Tests for sub-agent construction (synchronous building blocks).

We use a MockLLMModel registered with ModelRegistry so LlmAgent's
``model_post_init`` (which resolves string model names via the registry)
succeeds for the test model names. Real Runner-driven spawning is out of
scope here — that requires a stub LLM and is better suited to integration
tests.
"""

from __future__ import annotations

from typing import List
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.agents.sub_agent import GENERAL_PURPOSE_AGENT
from trpc_agent_sdk.agents.sub_agent import SubAgentArchetype
from trpc_agent_sdk.agents.sub_agent import SubAgentConfig
from trpc_agent_sdk.agents.sub_agent._constants import ISOLATION_DEFAULTS
from trpc_agent_sdk.agents.sub_agent._runner import _BorrowedToolSet
from trpc_agent_sdk.agents.sub_agent._runner import _build_sub_agent
from trpc_agent_sdk.agents.sub_agent._runner import _collect_parent_events
from trpc_agent_sdk.agents.sub_agent._runner import _event_is_model_visible
from trpc_agent_sdk.agents.sub_agent._runner import _extract_final_text
from trpc_agent_sdk.agents.sub_agent._runner import _forward_artifacts
from trpc_agent_sdk.agents.sub_agent._runner import _is_user_text_event
from trpc_agent_sdk.agents.sub_agent._runner import _materialize_tools
from trpc_agent_sdk.agents.sub_agent._runner import _resolve_model
from trpc_agent_sdk.models import LLMModel
from trpc_agent_sdk.models import LlmResponse
from trpc_agent_sdk.models import ModelRegistry
from trpc_agent_sdk.tools import BaseTool
from trpc_agent_sdk.tools import BaseToolSet
from trpc_agent_sdk.tools import GrepTool
from trpc_agent_sdk.tools import ReadTool


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-dynamic-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=None)

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def _register_mock_model():
    original = ModelRegistry._registry.copy()
    ModelRegistry.register(MockLLMModel)
    yield
    ModelRegistry._registry = original


def _parent_ctx_with_model(model: str) -> MagicMock:
    parent_ctx = MagicMock()
    parent_agent = MagicMock()
    parent_agent.model = model
    parent_agent.generate_content_config = None
    parent_agent.parallel_tool_calls = False
    parent_ctx.agent = parent_agent
    return parent_ctx


# --- _materialize_tools ----------------------------------------------------


def test_materialize_tools_factories_to_instances() -> None:
    out = _materialize_tools((ReadTool,))
    assert len(out) == 1
    assert isinstance(out[0], BaseTool)


def test_materialize_tools_passes_instances_through() -> None:
    inst = ReadTool()
    out = _materialize_tools((inst,))
    assert out == [inst]


def test_materialize_tools_rejects_garbage() -> None:
    with pytest.raises(TypeError):
        _materialize_tools(("not-a-tool",))


# --- _resolve_model -------------------------------------------------


def test_resolve_model_from_agent_config() -> None:
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    config = SubAgentConfig(model="from-config")
    assert _resolve_model(config, parent_ctx) == "from-config"


def test_resolve_model_falls_back_to_parent() -> None:
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    assert _resolve_model(None, parent_ctx) == "test-dynamic-parent"


def test_resolve_model_raises_when_missing() -> None:
    parent_ctx = _parent_ctx_with_model("")
    with pytest.raises(ValueError, match="cannot resolve sub-agent model"):
        _resolve_model(None, parent_ctx)


# --- _build_sub_agent ------------------------------------------------------


def test_build_sub_agent_uses_agent_config_model() -> None:
    """SubAgentConfig.model is used when set."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(model="test-dynamic-default"),
    )
    assert isinstance(agent.model, LLMModel)


def test_build_sub_agent_falls_back_to_parent_model() -> None:
    """Falls back to parent model when agent_config.model is not set."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)
    assert isinstance(agent.model, LLMModel)


def test_build_sub_agent_inherits_parallel_tool_calls_from_parent() -> None:
    """parallel_tool_calls inherits from parent when not in agent_config."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.parallel_tool_calls = True
    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)
    assert agent.parallel_tool_calls is True


def test_build_sub_agent_applies_isolation_defaults() -> None:
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)
    for field, expected in ISOLATION_DEFAULTS.items():
        actual = getattr(agent, field)
        assert actual == expected, f"{field}: expected {expected!r}, got {actual!r}"


def test_build_sub_agent_name_format() -> None:
    """Hyphens in archetype.name are normalized to underscores so the
    LlmAgent name remains a valid Python identifier."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)
    assert agent.name == "subagent_general_purpose"


def test_build_sub_agent_no_callbacks() -> None:
    """Strict isolation: parent callbacks are not inherited."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)
    assert agent.before_model_callback is None
    assert agent.after_model_callback is None
    assert agent.before_tool_callback is None
    assert agent.after_tool_callback is None
    assert agent.before_agent_callback is None
    assert agent.after_agent_callback is None


def test_build_sub_agent_no_output_key() -> None:
    """Strict isolation: sub-agent must not write into parent state."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)
    assert agent.output_key is None


def test_build_sub_agent_filters_out_nesting_tools() -> None:
    """Neither SpawnSubAgentTool nor DynamicAgentTool must reach the sub-agent.

    When tools=None (inherit parent), the parent may have either tool. The
    1-level cap must strip both.
    """
    from trpc_agent_sdk.agents.sub_agent import DynamicAgentTool
    from trpc_agent_sdk.agents.sub_agent import SpawnSubAgentTool

    arc = SubAgentArchetype(name="custom", description="d", instruction="i", tools=None)
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [
        ReadTool(),
        SpawnSubAgentTool(with_default=False),
        DynamicAgentTool(),
    ]
    agent = _build_sub_agent(arc, parent_ctx)
    tool_names = [type(t).__name__ for t in agent.tools]
    assert "DynamicAgentTool" not in tool_names
    assert "SpawnSubAgentTool" not in tool_names
    assert "ReadTool" in tool_names  # inherited tool still present


# --- _BorrowedToolSet -------------------------------------------------------


@pytest.mark.asyncio
async def test_borrowed_toolset_proxies_get_tools() -> None:
    """_BorrowedToolSet.get_tools() delegates to the inner toolset."""

    class _FakeToolSet(BaseToolSet):
        async def get_tools(self, invocation_context=None):
            return [ReadTool()]

    inner = _FakeToolSet()
    borrowed = _BorrowedToolSet(inner)
    tools = await borrowed.get_tools()
    assert len(tools) == 1
    assert isinstance(tools[0], ReadTool)


@pytest.mark.asyncio
async def test_borrowed_toolset_close_is_noop() -> None:
    """_BorrowedToolSet.close() must not close the inner toolset."""
    closed = []

    class _FakeToolSet(BaseToolSet):
        async def get_tools(self, invocation_context=None):
            return []

        async def close(self):
            closed.append(True)

    inner = _FakeToolSet()
    borrowed = _BorrowedToolSet(inner)
    await borrowed.close()
    assert closed == [], "inner toolset must not be closed via _BorrowedToolSet"


def test_agent_config_applied_to_sub_agent() -> None:
    """agent_config fields are forwarded to the LlmAgent constructor."""
    from trpc_agent_sdk.types import GenerateContentConfig

    gen_config = GenerateContentConfig(temperature=0.1)
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(
            generate_content_config=gen_config,
            parallel_tool_calls=True,
        ),
    )
    assert agent.generate_content_config is gen_config
    assert agent.parallel_tool_calls is True


# --- skill_repository tracks SkillToolSet ---------------------------------


def test_build_sub_agent_skill_repo_none_without_skill_toolset() -> None:
    """When tools contain no SkillToolSet, sub-agent's skill_repository is None."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]
    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)
    assert agent.skill_repository is None


def test_build_sub_agent_skill_repo_from_inherited_skill_toolset() -> None:
    """When parent has a SkillToolSet, sub-agent inherits its repository."""
    pytest.importorskip("trpc_agent_sdk.skills")
    from trpc_agent_sdk.skills import SkillToolSet

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_skill_toolset = SkillToolSet()
    parent_ctx.agent.tools = [ReadTool(), parent_skill_toolset]

    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)
    assert agent.skill_repository is parent_skill_toolset.repository


def test_build_sub_agent_skill_repo_from_archetype_skill_toolset() -> None:
    """When archetype.tools contains a SkillToolSet, its repository is used."""
    pytest.importorskip("trpc_agent_sdk.skills")
    from trpc_agent_sdk.skills import SkillToolSet

    archetype_skill_toolset = SkillToolSet()
    arc = SubAgentArchetype(
        name="custom",
        description="d",
        instruction="i",
        tools=(ReadTool, archetype_skill_toolset),
    )
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = []  # parent has no SkillToolSet

    agent = _build_sub_agent(arc, parent_ctx)
    assert agent.skill_repository is archetype_skill_toolset.repository


def test_agent_config_does_not_override_instruction() -> None:
    """agent_config cannot override archetype instruction."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(),
    )
    assert agent.instruction == GENERAL_PURPOSE_AGENT.instruction


def test_isolation_defaults_always_win() -> None:
    """ISOLATION_DEFAULTS override agent_config."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(),
    )
    assert agent.output_key is None


def test_agent_config_non_none_is_passed() -> None:
    """Non-None agent_config values override LlmAgent defaults."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(parallel_tool_calls=True),
    )
    assert agent.parallel_tool_calls is True


def test_collect_parent_events_empty_session() -> None:
    """Empty session returns empty list."""
    from trpc_agent_sdk.agents.sub_agent._runner import _collect_parent_events
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.session.events = []
    assert _collect_parent_events(parent_ctx, max_parent_history_turns=3) == []


def test_collect_parent_events_no_max_turns() -> None:
    """max_parent_history_turns=0 returns empty list."""
    from trpc_agent_sdk.agents.sub_agent._runner import _collect_parent_events
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.session.events = [MagicMock(content=MagicMock(), author="user", is_model_visible=True)]
    assert _collect_parent_events(parent_ctx, max_parent_history_turns=0) == []


def test_event_is_model_visible_calls_method() -> None:
    """_event_is_model_visible calls is_model_visible() if it's callable."""
    from trpc_agent_sdk.agents.sub_agent._runner import _event_is_model_visible
    called = []
    event = MagicMock(is_model_visible=lambda: called.append(True) or True)
    assert _event_is_model_visible(event) is True
    assert called  # method was actually called


def test_collect_parent_events_all_turns() -> None:
    """max_parent_history_turns=None returns all visible events with content."""
    from trpc_agent_sdk.agents.sub_agent._runner import _collect_parent_events
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    e1 = MagicMock(content=MagicMock(), author="user", is_model_visible=True)
    e2 = MagicMock(content=MagicMock(), author="model", is_model_visible=True)
    parent_ctx.session.events = [e1, e2]
    result = _collect_parent_events(parent_ctx, max_parent_history_turns=None)
    assert len(result) == 2


def test_build_sub_agent_history_fields_not_forwarded_to_llm_agent() -> None:
    """include_parent_history and max_parent_history_turns are not passed to LlmAgent."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(include_parent_history=True, max_parent_history_turns=3),
    )
    assert not hasattr(agent, "include_parent_history")
    assert not hasattr(agent, "max_parent_history_turns")


def test_build_sub_agent_no_parent_history_has_no_effect() -> None:
    """include_parent_history=False should not affect LlmAgent construction."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(include_parent_history=False),
    )
    assert agent.name == "subagent_general_purpose"  # builds without error


def test_build_sub_agent_wraps_parent_toolsets_when_tools_none() -> None:
    """When archetype.tools is None, BaseToolSet instances from the parent are
    wrapped in _BorrowedToolSet so sub_runner.close() cannot close them."""

    class _FakeToolSet(BaseToolSet):
        async def get_tools(self, invocation_context=None):
            return []

    fake_toolset = _FakeToolSet()
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool(), fake_toolset]

    agent = _build_sub_agent(GENERAL_PURPOSE_AGENT, parent_ctx)

    toolset_items = [t for t in agent.tools if isinstance(t, BaseToolSet)]
    assert len(toolset_items) == 1
    assert isinstance(toolset_items[0], _BorrowedToolSet)


def test_build_sub_agent_max_turns_not_forwarded_to_llm_agent() -> None:
    """max_turns is not an LlmAgent parameter and should not leak."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(max_turns=5),
    )
    assert not hasattr(agent, "max_turns")


def test_build_sub_agent_max_turns_none_has_no_effect() -> None:
    """max_turns=None should not affect LlmAgent construction."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    agent = _build_sub_agent(
        GENERAL_PURPOSE_AGENT, parent_ctx,
        agent_config=SubAgentConfig(max_turns=None),
    )
    assert agent.name == "subagent_general_purpose"


# --- _is_user_text_event -----------------------------------------------------


def test_is_user_text_event_true() -> None:
    event = MagicMock()
    event.author = "user"
    event.content.parts = [MagicMock(text="hello")]
    assert _is_user_text_event(event) is True


def test_is_user_text_event_wrong_author() -> None:
    event = MagicMock()
    event.author = "model"
    event.content.parts = [MagicMock(text="hello")]
    assert _is_user_text_event(event) is False


def test_is_user_text_event_no_content() -> None:
    event = MagicMock()
    event.author = "user"
    event.content = None
    assert _is_user_text_event(event) is False


def test_is_user_text_event_no_parts() -> None:
    event = MagicMock()
    event.author = "user"
    event.content.parts = []
    assert _is_user_text_event(event) is False


def test_is_user_text_event_no_text_in_parts() -> None:
    event = MagicMock()
    event.author = "user"
    event.content.parts = [MagicMock(text=None)]
    assert _is_user_text_event(event) is False


# --- _extract_final_text -----------------------------------------------------


def test_extract_final_text_concatenates_parts() -> None:
    event = MagicMock()
    event.content.parts = [MagicMock(text="Hello"), MagicMock(text="World")]
    assert _extract_final_text(event) == "Hello\nWorld"


def test_extract_final_text_none_event() -> None:
    assert _extract_final_text(None) == ""


def test_extract_final_text_no_content() -> None:
    event = MagicMock()
    event.content = None
    assert _extract_final_text(event) == ""


def test_extract_final_text_no_parts() -> None:
    event = MagicMock()
    event.content.parts = []
    assert _extract_final_text(event) == ""


# --- _collect_parent_events — turn counting ----------------------------------


def test_collect_parent_events_counts_turns() -> None:
    """max_parent_history_turns=1 returns only the last turn's events."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")

    e1 = MagicMock(author="user", content=MagicMock(parts=[MagicMock(text="first")]))
    e1.is_model_visible = True
    e2 = MagicMock(author="model", content=MagicMock(parts=[MagicMock(text="reply")]))
    e2.is_model_visible = True
    e3 = MagicMock(author="user", content=MagicMock(parts=[MagicMock(text="second")]))
    e3.is_model_visible = True
    e4 = MagicMock(author="model", content=MagicMock(parts=[MagicMock(text="reply2")]))
    e4.is_model_visible = True

    parent_ctx.session.events = [e1, e2, e3, e4]
    result = _collect_parent_events(parent_ctx, max_parent_history_turns=1)
    # Only events from the last turn (2nd user message onward) should be included.
    assert result == [e3, e4]


def test_collect_parent_events_all_turns_with_model_and_system() -> None:
    """Events include system messages that aren't user text — they still count as visible."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")

    e1 = MagicMock(author="user", content=MagicMock(parts=[MagicMock(text="hi")]))
    e1.is_model_visible = True
    e2 = MagicMock(author="model", content=MagicMock(parts=[MagicMock(text="ok")]))
    e2.is_model_visible = True

    parent_ctx.session.events = [e1, e2]
    result = _collect_parent_events(parent_ctx, max_parent_history_turns=None)
    assert result == [e1, e2]


# --- _forward_artifacts ------------------------------------------------------


@pytest.mark.asyncio
async def test_forward_artifacts_no_artifact_service() -> None:
    """When sub_runner has no artifact_service, nothing happens."""
    sub_runner = MagicMock()
    sub_runner.artifact_service = None
    sub_session = MagicMock()
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")

    await _forward_artifacts(sub_runner, sub_session, parent_ctx)
    # Should return silently without errors
    parent_ctx.save_artifact.assert_not_called()


@pytest.mark.asyncio
async def test_forward_artifacts_copies_files() -> None:
    sub_runner = MagicMock()
    sub_session = MagicMock()
    sub_session.app_name = "sub_app"
    sub_session.user_id = "sub_user"
    sub_session.id = "sub_session_id"

    from unittest.mock import AsyncMock

    async def _list_keys(artifact_id=None):
        return ["file1.txt", "file2.txt"]

    sub_runner.artifact_service.list_artifact_keys = _list_keys
    sub_runner.artifact_service.load_artifact = AsyncMock(return_value="artifact_data")

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.save_artifact = AsyncMock()

    await _forward_artifacts(sub_runner, sub_session, parent_ctx)
    assert sub_runner.artifact_service.load_artifact.call_count == 2
    assert parent_ctx.save_artifact.call_count == 2


# --- run_subagent ------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_subagent_with_mocked_runner() -> None:
    """run_subagent spawns a Runner, iterates events, returns final text."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]
    parent_ctx.session.app_name = "test_app"
    parent_ctx.artifact_service = None

    # Build a fake event stream: a partial event followed by a final model event.
    partial_event = MagicMock()
    partial_event.content = MagicMock(role="model")
    partial_event.partial = True
    partial_event.is_error = MagicMock(return_value=False)

    final_event = MagicMock()
    final_event.content = MagicMock(role="model", parts=[MagicMock(text="Sub-agent answer.")])
    final_event.partial = False
    final_event.is_error = MagicMock(return_value=False)

    event_stream = [partial_event, final_event]

    async def _fake_run_async(*args, **kwargs):
        for event in event_stream:
            yield event

    mock_runner_cls = MagicMock()
    mock_runner_instance = MagicMock()
    mock_runner_instance.run_async = _fake_run_async
    mock_runner_instance.session_service = MagicMock()
    mock_runner_instance.session_service.create_session = AsyncMock()
    mock_runner_instance.session_service.append_event = AsyncMock()
    mock_runner_instance.artifact_service = None
    mock_runner_instance.close = AsyncMock()
    mock_runner_cls.return_value = mock_runner_instance

    with patch("trpc_agent_sdk.runners.Runner", mock_runner_cls):
        from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
        result = await run_subagent(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
        )

    assert result == "Sub-agent answer."
    mock_runner_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_subagent_cancelled_returns_marker() -> None:
    from unittest.mock import AsyncMock
    from unittest.mock import patch

    from trpc_agent_sdk.exceptions import RunCancelledException

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]
    parent_ctx.session.app_name = "test_app"
    parent_ctx.artifact_service = None

    mock_runner_instance = MagicMock()
    mock_runner_instance.session_service = MagicMock()
    mock_runner_instance.session_service.create_session = AsyncMock()
    mock_runner_instance.artifact_service = None
    mock_runner_instance.close = AsyncMock()
    mock_runner_instance.run_async = MagicMock()
    mock_runner_instance.run_async.side_effect = RunCancelledException()

    mock_runner_cls = MagicMock(return_value=mock_runner_instance)

    with patch("trpc_agent_sdk.runners.Runner", mock_runner_cls):
        from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
        result = await run_subagent(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
        )

    assert result == "[sub-agent cancelled]"
    mock_runner_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_subagent_max_turns_enforced() -> None:
    """max_turns stops the sub-agent and appends a note."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]
    parent_ctx.session.app_name = "test_app"
    parent_ctx.artifact_service = None

    # First event counts as turn 1; second event exceeds max_turns=1.
    event = MagicMock()
    event.content = MagicMock(role="model", parts=[MagicMock(text="Iteration 1.")])
    event.partial = False
    event.is_error = MagicMock(return_value=False)

    async def _fake_run_async(*args, **kwargs):
        yield event

    mock_runner_instance = MagicMock()
    mock_runner_instance.run_async = _fake_run_async
    mock_runner_instance.session_service = MagicMock()
    mock_runner_instance.session_service.create_session = AsyncMock()
    mock_runner_instance.session_service.append_event = AsyncMock()
    mock_runner_instance.artifact_service = None
    mock_runner_instance.close = AsyncMock()

    mock_runner_cls = MagicMock(return_value=mock_runner_instance)

    with patch("trpc_agent_sdk.runners.Runner", mock_runner_cls):
        from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
        result = await run_subagent(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
            agent_config=SubAgentConfig(max_turns=1),
        )

    assert "[sub-agent stopped: max turns reached]" in result
    mock_runner_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_subagent_build_error_returns_error_dict() -> None:
    """When _build_sub_agent raises, run_subagent catches it and returns an error dict."""
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.model = None  # force resolve_model to raise
    parent_ctx.agent.tools = []
    parent_ctx.session.app_name = "test_app"

    from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
    result = await run_subagent(
        parent_ctx=parent_ctx,
        archetype=GENERAL_PURPOSE_AGENT,
        prompt="Do something.",
    )

    assert isinstance(result, dict)
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_run_subagent_injects_parent_history() -> None:
    """When include_parent_history=True, parent events are injected into sub-session."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]
    parent_ctx.session.app_name = "test_app"
    parent_ctx.artifact_service = None

    event = MagicMock()
    event.content = MagicMock(role="model", parts=[MagicMock(text="Done.")])
    event.partial = False
    event.is_error = MagicMock(return_value=False)

    parent_event = MagicMock()
    parent_event.author = "user"
    parent_event.content = MagicMock(parts=[MagicMock(text="parent history")])
    parent_event.is_model_visible = True
    parent_ctx.session.events = [parent_event]

    async def _fake_run_async(*args, **kwargs):
        yield event

    mock_runner_instance = MagicMock()
    mock_runner_instance.run_async = _fake_run_async
    mock_runner_instance.session_service = MagicMock()
    mock_runner_instance.session_service.create_session = AsyncMock()
    mock_runner_instance.session_service.append_event = AsyncMock()
    mock_runner_instance.artifact_service = None
    mock_runner_instance.close = AsyncMock()

    mock_runner_cls = MagicMock(return_value=mock_runner_instance)

    with patch("trpc_agent_sdk.runners.Runner", mock_runner_cls):
        from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
        await run_subagent(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
            agent_config=SubAgentConfig(include_parent_history=True),
        )

    mock_runner_instance.session_service.append_event.assert_called()


# --- _build_sub_agent with tool_filter ---------------------------------------


def test_build_sub_agent_with_tool_filter() -> None:
    """tool_filter restricts tools by name — matching tools kept, others dropped."""
    from trpc_agent_sdk.agents.sub_agent._runner import _build_sub_agent

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool(), GrepTool()]

    arc = SubAgentArchetype(name="filtered", description="d", instruction="i", tools=None)
    agent = _build_sub_agent(arc, parent_ctx, tool_filter=["Read"])

    tool_names = [getattr(t, "name", None) for t in agent.tools]
    assert "Read" in tool_names
    assert "Grep" not in tool_names


def test_build_sub_agent_with_tool_filter_no_matches() -> None:
    """When no tools match the filter, only _BorrowedToolSet instances remain."""
    from trpc_agent_sdk.agents.sub_agent._runner import _build_sub_agent

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]

    arc = SubAgentArchetype(name="filtered", description="d", instruction="i", tools=None)
    agent = _build_sub_agent(arc, parent_ctx, tool_filter=["NonExistent"])

    # Only non-BorrowedToolSet tools (ReadTool) get filtered; no matches → empty.
    regular_tools = [t for t in agent.tools if not isinstance(t, _BorrowedToolSet)]
    assert len(regular_tools) == 0


def test_build_sub_agent_with_tool_filter_and_fixed_tools() -> None:
    """tool_filter works with archetype-provided fixed tools (not inherited)."""
    from trpc_agent_sdk.agents.sub_agent._runner import _build_sub_agent

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = []  # parent tools are irrelevant when archetype has its own

    arc = SubAgentArchetype(
        name="filtered",
        description="d",
        instruction="i",
        tools=(ReadTool(), GrepTool()),
    )
    agent = _build_sub_agent(arc, parent_ctx, tool_filter=["Grep"])

    tool_names = [getattr(t, "name", None) for t in agent.tools]
    assert "Grep" in tool_names
    assert "Read" not in tool_names


def test_build_sub_agent_tool_filter_preserves_borrowed_toolsets() -> None:
    """When parent has BaseToolSet instances (wrapped in _BorrowedToolSet)
    and tool_filter is applied, those wrappers are always kept."""
    from trpc_agent_sdk.agents.sub_agent._runner import _build_sub_agent

    class _FakeToolSet(BaseToolSet):
        async def get_tools(self, invocation_context=None):
            return []

    fake_toolset = _FakeToolSet()
    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool(), fake_toolset]

    arc = SubAgentArchetype(name="filtered", description="d", instruction="i", tools=None)
    agent = _build_sub_agent(arc, parent_ctx, tool_filter=["Read"])

    # ReadTool should be kept (in filter), _BorrowedToolSet should be kept (always preserved)
    tool_names = [getattr(t, "name", None) for t in agent.tools]
    assert "Read" in tool_names
    borrowed = [t for t in agent.tools if isinstance(t, _BorrowedToolSet)]
    assert len(borrowed) == 1


# --- run_subagent error paths -------------------------------------------------


@pytest.mark.asyncio
async def test_run_subagent_runtime_exception() -> None:
    """Non-build, non-cancelled exceptions during run_async return error dict."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]
    parent_ctx.session.app_name = "test_app"
    parent_ctx.artifact_service = None

    mock_runner_instance = MagicMock()
    mock_runner_instance.session_service = MagicMock()
    mock_runner_instance.session_service.create_session = AsyncMock()
    mock_runner_instance.artifact_service = None
    mock_runner_instance.close = AsyncMock()

    async def _failing_run(*args, **kwargs):
        if False:
            yield  # make this an async generator
        raise RuntimeError("simulated runtime failure")

    mock_runner_instance.run_async = _failing_run

    mock_runner_cls = MagicMock(return_value=mock_runner_instance)

    with patch("trpc_agent_sdk.runners.Runner", mock_runner_cls):
        from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
        result = await run_subagent(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
        )

    assert isinstance(result, dict)
    assert result["status"] == "error"
    assert "simulated runtime failure" in result["message"]
    mock_runner_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_subagent_close_failure() -> None:
    """Exception from sub_runner.close() is caught and logged, not propagated."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]
    parent_ctx.session.app_name = "test_app"
    parent_ctx.artifact_service = None

    final_event = MagicMock()
    final_event.content = MagicMock(role="model", parts=[MagicMock(text="Done.")])
    final_event.partial = False
    final_event.is_error = MagicMock(return_value=False)

    async def _fake_run(*args, **kwargs):
        yield final_event

    mock_runner_instance = MagicMock()
    mock_runner_instance.run_async = _fake_run
    mock_runner_instance.session_service = MagicMock()
    mock_runner_instance.session_service.create_session = AsyncMock()
    mock_runner_instance.session_service.append_event = AsyncMock()
    mock_runner_instance.artifact_service = None
    mock_runner_instance.close = AsyncMock(side_effect=RuntimeError("close failed"))

    mock_runner_cls = MagicMock(return_value=mock_runner_instance)

    with patch("trpc_agent_sdk.runners.Runner", mock_runner_cls):
        from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
        result = await run_subagent(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
        )

    # The result should still be the final text — close errors don't affect output.
    assert result == "Done."
    mock_runner_instance.close.assert_called_once()


@pytest.mark.asyncio
async def test_run_subagent_max_turns_no_last_event() -> None:
    """When max_turns reached but no event was produced, returns the stop marker."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch

    parent_ctx = _parent_ctx_with_model("test-dynamic-parent")
    parent_ctx.agent.tools = [ReadTool()]
    parent_ctx.session.app_name = "test_app"
    parent_ctx.artifact_service = None

    # Produce a single model event so max_turns=1 triggers immediately.
    event = MagicMock()
    event.content = MagicMock(role="model", parts=[])
    event.partial = False
    event.is_error = MagicMock(return_value=False)

    async def _fake_run(*args, **kwargs):
        yield event

    mock_runner_instance = MagicMock()
    mock_runner_instance.run_async = _fake_run
    mock_runner_instance.session_service = MagicMock()
    mock_runner_instance.session_service.create_session = AsyncMock()
    mock_runner_instance.session_service.append_event = AsyncMock()
    mock_runner_instance.artifact_service = None
    mock_runner_instance.close = AsyncMock()

    mock_runner_cls = MagicMock(return_value=mock_runner_instance)

    with patch("trpc_agent_sdk.runners.Runner", mock_runner_cls):
        from trpc_agent_sdk.agents.sub_agent._runner import run_subagent
        result = await run_subagent(
            parent_ctx=parent_ctx,
            archetype=GENERAL_PURPOSE_AGENT,
            prompt="Do something.",
            agent_config=SubAgentConfig(max_turns=1),
        )

    assert "[sub-agent stopped: max turns reached]" in result
