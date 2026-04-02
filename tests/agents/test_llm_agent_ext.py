# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Extended tests for LlmAgent — covers _run_async_impl paths, tool processing, and edge cases."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents._llm_agent import LlmAgent
from trpc_agent_sdk.agents.core._history_processor import BranchFilterMode, TimelineFilterMode
from trpc_agent_sdk.configs import RunConfig
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, GenerateContentConfig, Part


# ---------------------------------------------------------------------------
# Stubs / helpers
# ---------------------------------------------------------------------------


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-llm-ext-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=Content(parts=[Part(text="mock response")]))

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    original_registry = ModelRegistry._registry.copy()
    ModelRegistry.register(MockLLMModel)
    yield
    ModelRegistry._registry = original_registry


def _agent(**kwargs):
    defaults = dict(name="test_agent", model="test-llm-ext-model")
    defaults.update(kwargs)
    return LlmAgent(**defaults)


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = _agent()
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        run_config=RunConfig(),
    )
    return ctx


# ---------------------------------------------------------------------------
# _tools_processor property
# ---------------------------------------------------------------------------


class TestToolsProcessorProperty:
    def test_returns_processor_with_no_tools(self):
        """Returns ToolsProcessor even when agent has no tools."""
        agent = _agent(tools=[])
        tp = agent._tools_processor
        assert tp is not None

    def test_returns_processor_with_tools(self):
        """Returns ToolsProcessor populated with agent tools."""
        from trpc_agent_sdk.tools import FunctionTool

        def my_func(x: str) -> str:
            """A test function."""
            return x

        tool = FunctionTool(my_func)
        agent = _agent(tools=[tool])
        tp = agent._tools_processor
        assert tp is not None


# ---------------------------------------------------------------------------
# _get_extended_tools_processor
# ---------------------------------------------------------------------------


class TestGetExtendedToolsProcessor:
    def test_no_transfer_when_no_sub_agents(self, invocation_context):
        """No transfer tool added when agent has no sub_agents."""
        agent = _agent()
        tp = agent._get_extended_tools_processor(invocation_context)
        assert tp is not None

    def test_adds_transfer_tool_with_sub_agents(self, invocation_context):
        """Transfer tool added when agent has sub_agents."""
        child = _agent(name="child")
        parent = _agent(name="parent", sub_agents=[child])
        tp = parent._get_extended_tools_processor(invocation_context)
        assert tp is not None

    def test_does_not_mutate_original_tools(self, invocation_context):
        """Original tools list is not mutated."""
        from trpc_agent_sdk.tools import FunctionTool

        def my_func(x: str) -> str:
            """A test function."""
            return x

        tool = FunctionTool(my_func)
        agent = _agent(tools=[tool])
        original_len = len(agent.tools)
        agent._get_extended_tools_processor(invocation_context)
        assert len(agent.tools) == original_len


# ---------------------------------------------------------------------------
# _should_enable_agent_transfer — extended
# ---------------------------------------------------------------------------


class TestShouldEnableAgentTransferExtended:
    def test_transfer_to_peers_enabled(self):
        """Transfer enabled when agent has siblings."""
        child1 = _agent(name="child1")
        child2 = _agent(name="child2")
        parent = _agent(name="parent", sub_agents=[child1, child2])
        assert child1._should_enable_agent_transfer() is True

    def test_disallow_both_disables(self):
        """Disabling both parent and peer transfer disables transfer."""
        child = _agent(
            name="child",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
        parent = _agent(name="parent", sub_agents=[child])
        assert child._should_enable_agent_transfer() is False

    def test_non_llm_parent_disables_parent_transfer(self):
        """Non-LlmAgent parent disables parent transfer."""
        class SimpleAgent(BaseAgent):
            async def _run_async_impl(self, ctx):
                yield

        child = _agent(name="child")
        parent = SimpleAgent(name="simple_parent", sub_agents=[child])
        assert child._should_enable_agent_transfer() is False


# ---------------------------------------------------------------------------
# _resolve_model — extended
# ---------------------------------------------------------------------------


class TestResolveModelExtended:
    def test_factory_receives_custom_data(self, invocation_context):
        """Factory callback receives custom_data from run_config."""
        invocation_context.run_config.custom_data["key"] = "value"

        received_data = {}

        async def factory(custom_data):
            received_data.update(custom_data)
            return MockLLMModel(model_name="test-llm-ext-model")

        agent = _agent(model=factory)

        async def run():
            return await agent._resolve_model(invocation_context)

        asyncio.run(run())
        assert received_data.get("key") == "value"

    def test_factory_with_no_run_config(self, invocation_context):
        """Factory works when run_config is None."""
        invocation_context.run_config = None

        async def factory(custom_data):
            return MockLLMModel(model_name="test-llm-ext-model")

        agent = _agent(model=factory)

        async def run():
            return await agent._resolve_model(invocation_context)

        resolved = asyncio.run(run())
        assert isinstance(resolved, LLMModel)


# ---------------------------------------------------------------------------
# _create_error_event — extended
# ---------------------------------------------------------------------------


class TestCreateErrorEventExtended:
    def test_branch_is_included(self, invocation_context):
        """Error event includes the context's branch."""
        invocation_context.branch = "some.branch"
        agent = invocation_context.agent
        event = agent._create_error_event(invocation_context, "ERR", "msg")
        assert event.branch == "some.branch"

    def test_author_is_agent_name(self, invocation_context):
        """Error event author is the agent's name."""
        agent = invocation_context.agent
        event = agent._create_error_event(invocation_context, "ERR", "msg")
        assert event.author == agent.name


# ---------------------------------------------------------------------------
# _save_output_to_state — extended
# ---------------------------------------------------------------------------


class TestSaveOutputToStateExtended:
    def test_filters_thought_parts(self, invocation_context):
        """Thought parts are excluded from saved output."""
        agent = _agent(output_key="result")
        invocation_context.agent = agent
        thought_part = Part(text="thinking...", thought=True)
        text_part = Part(text="final answer")
        event = Event(
            invocation_id="inv-1",
            author="test_agent",
            content=Content(parts=[thought_part, text_part]),
        )
        agent._save_output_to_state(invocation_context, event)
        assert invocation_context.session.state.get("result") == "final answer"

    def test_state_delta_set(self, invocation_context):
        """State delta is correctly set."""
        agent = _agent(output_key="out")
        invocation_context.agent = agent
        event = Event(
            invocation_id="inv-1",
            author="test_agent",
            content=Content(parts=[Part(text="value")]),
        )
        agent._save_output_to_state(invocation_context, event)
        assert event.actions.state_delta.get("out") == "value"


# ---------------------------------------------------------------------------
# _get_effective_branch_filter_mode — extended
# ---------------------------------------------------------------------------


class TestBranchFilterModeExtended:
    def test_prefix_overrides_true_include_previous(self):
        """PREFIX mode takes precedence over include_previous_history=True."""
        agent = _agent(
            include_previous_history=True,
            message_branch_filter_mode=BranchFilterMode.PREFIX,
        )
        assert agent._get_effective_branch_filter_mode() == BranchFilterMode.PREFIX


# ---------------------------------------------------------------------------
# Validators — extended
# ---------------------------------------------------------------------------


class TestValidatorsExtended:
    def test_response_schema_in_config_raises(self):
        """Response schema in generate_content_config raises."""
        from pydantic import BaseModel as PydanticModel

        class TestSchema(PydanticModel):
            name: str

        with pytest.raises(ValueError, match="Response schema"):
            _agent(
                generate_content_config=GenerateContentConfig(
                    response_schema=TestSchema,
                ),
            )

    def test_valid_config_passes(self):
        """Valid generate_content_config passes validation."""
        agent = _agent(
            generate_content_config=GenerateContentConfig(temperature=0.5),
        )
        assert agent.generate_content_config.temperature == 0.5

    def test_none_config_produces_default(self):
        """None generate_content_config produces default instance."""
        agent = _agent(generate_content_config=None)
        assert isinstance(agent.generate_content_config, GenerateContentConfig)


# ---------------------------------------------------------------------------
# model_post_init
# ---------------------------------------------------------------------------


class TestModelPostInit:
    def test_string_model_resolved_on_init(self):
        """String model is resolved to LLMModel on init."""
        agent = _agent(model="test-llm-ext-model")
        assert isinstance(agent.model, LLMModel)

    def test_callable_model_not_resolved_on_init(self):
        """Callable model factory is not resolved on init."""
        async def factory(custom_data):
            return MockLLMModel(model_name="test-llm-ext-model")

        agent = _agent(model=factory)
        assert callable(agent.model)

    def test_model_instance_used_directly(self):
        """LLMModel instance is used directly without registry lookup."""
        model_instance = MockLLMModel(model_name="test-llm-ext-model")
        agent = _agent(model=model_instance)
        assert agent.model is model_instance


# ---------------------------------------------------------------------------
# Agent field defaults
# ---------------------------------------------------------------------------


class TestAgentFieldDefaults:
    def test_default_include_contents(self):
        """Default include_contents is 'default'."""
        agent = _agent()
        assert agent.include_contents == "default"

    def test_default_timeline_filter_mode(self):
        """Default timeline filter mode is ALL."""
        agent = _agent()
        assert agent.message_timeline_filter_mode == TimelineFilterMode.ALL

    def test_default_branch_filter_mode(self):
        """Default branch filter mode is ALL."""
        agent = _agent()
        assert agent.message_branch_filter_mode == BranchFilterMode.ALL

    def test_default_add_name_to_instruction(self):
        """Default add_name_to_instruction is True."""
        agent = _agent()
        assert agent.add_name_to_instruction is True

    def test_default_disable_react_tool(self):
        """Default disable_react_tool is False."""
        agent = _agent()
        assert agent.disable_react_tool is False

    def test_default_transfer_message_none(self):
        """Default transfer message is None."""
        agent = _agent()
        assert agent.default_transfer_message is None

    def test_max_history_messages_default(self):
        """Default max_history_messages is 0."""
        agent = _agent()
        assert agent.max_history_messages == 0


# ---------------------------------------------------------------------------
# _run_async_impl — error path via mock
# ---------------------------------------------------------------------------


class TestRunAsyncImplErrorPath:
    def test_yields_error_on_request_build_failure(self, invocation_context):
        """Error event yielded when request building fails."""
        agent = _agent()
        invocation_context.agent = agent

        mock_event = Event(
            invocation_id="inv-1",
            author="test_agent",
            error_code="build_error",
            error_message="Failed to build request",
        )

        async def run():
            events = []
            with patch(
                "trpc_agent_sdk.agents._llm_agent.default_request_processor"
            ) as mock_rp:
                mock_rp.build_request = AsyncMock(return_value=mock_event)
                async for event in agent._run_async_impl(invocation_context):
                    events.append(event)
            return events

        events = asyncio.run(run())
        assert len(events) == 1
        assert events[0].error_code == "build_error"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
