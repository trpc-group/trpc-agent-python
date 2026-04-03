# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for LlmAgent configuration and helper methods."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.agents._llm_agent import LlmAgent
from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents.core._history_processor import BranchFilterMode
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, GenerateContentConfig, Part


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-llm-.*"]

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


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = LlmAgent(name="test_llm", model="test-llm-model")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )
    return ctx


# ---------------------------------------------------------------------------
# LlmAgent initialization
# ---------------------------------------------------------------------------


class TestLlmAgentInit:
    def test_default_values(self):
        agent = LlmAgent(name="test", model="test-llm-model")
        assert agent.instruction == ""
        assert agent.tools == []
        assert agent.parallel_tool_calls is False
        assert agent.include_contents == "default"
        assert agent.include_previous_history is True
        assert agent.max_history_messages == 0
        assert agent.output_schema is None
        assert agent.output_key is None
        assert agent.planner is None
        assert agent.before_model_callback is None
        assert agent.after_model_callback is None
        assert agent.before_tool_callback is None
        assert agent.after_tool_callback is None
        assert agent.add_name_to_instruction is True
        assert agent.disable_react_tool is False
        assert agent.default_transfer_message is None

    def test_model_string_resolves(self):
        agent = LlmAgent(name="test", model="test-llm-model")
        assert isinstance(agent.model, LLMModel)

    def test_model_instance_used_directly(self):
        model_instance = MockLLMModel(model_name="test-llm-model")
        agent = LlmAgent(name="test", model=model_instance)
        assert agent.model is model_instance

    def test_callable_model_not_resolved_on_init(self):
        async def model_factory(custom_data):
            return MockLLMModel(model_name="test-llm-model")

        agent = LlmAgent(name="test", model=model_factory)
        assert callable(agent.model)

    def test_instruction_can_be_string(self):
        agent = LlmAgent(name="test", model="test-llm-model", instruction="Be helpful")
        assert agent.instruction == "Be helpful"


# ---------------------------------------------------------------------------
# _get_effective_branch_filter_mode
# ---------------------------------------------------------------------------


class TestGetEffectiveBranchFilterMode:
    def test_default_returns_all(self):
        agent = LlmAgent(name="test", model="test-llm-model")
        assert agent._get_effective_branch_filter_mode() == BranchFilterMode.ALL

    def test_explicit_branch_mode_takes_precedence(self):
        agent = LlmAgent(
            name="test",
            model="test-llm-model",
            message_branch_filter_mode=BranchFilterMode.EXACT,
        )
        assert agent._get_effective_branch_filter_mode() == BranchFilterMode.EXACT

    def test_include_previous_history_false_gives_exact(self):
        agent = LlmAgent(
            name="test",
            model="test-llm-model",
            include_previous_history=False,
        )
        assert agent._get_effective_branch_filter_mode() == BranchFilterMode.EXACT

    def test_include_previous_history_true_gives_all(self):
        agent = LlmAgent(
            name="test",
            model="test-llm-model",
            include_previous_history=True,
        )
        assert agent._get_effective_branch_filter_mode() == BranchFilterMode.ALL

    def test_prefix_mode_overrides_include_previous(self):
        agent = LlmAgent(
            name="test",
            model="test-llm-model",
            include_previous_history=False,
            message_branch_filter_mode=BranchFilterMode.PREFIX,
        )
        assert agent._get_effective_branch_filter_mode() == BranchFilterMode.PREFIX


# ---------------------------------------------------------------------------
# _should_enable_agent_transfer
# ---------------------------------------------------------------------------


class TestShouldEnableAgentTransfer:
    def test_no_sub_agents_no_parent(self):
        agent = LlmAgent(name="test", model="test-llm-model")
        assert agent._should_enable_agent_transfer() is False

    def test_with_sub_agents(self):
        child = LlmAgent(name="child", model="test-llm-model")
        parent = LlmAgent(name="parent", model="test-llm-model", sub_agents=[child])
        assert parent._should_enable_agent_transfer() is True

    def test_with_llm_parent(self):
        child = LlmAgent(name="child", model="test-llm-model")
        parent = LlmAgent(name="parent", model="test-llm-model", sub_agents=[child])
        assert child._should_enable_agent_transfer() is True

    def test_disallow_transfer_to_parent(self):
        child = LlmAgent(
            name="child",
            model="test-llm-model",
            disallow_transfer_to_parent=True,
            disallow_transfer_to_peers=True,
        )
        parent = LlmAgent(name="parent", model="test-llm-model", sub_agents=[child])
        # Still True because parent has sub_agents (child itself)
        # But child should not enable transfer since both are disallowed and no sub_agents
        assert child._should_enable_agent_transfer() is False


# ---------------------------------------------------------------------------
# _is_llm_agent
# ---------------------------------------------------------------------------


class TestIsLlmAgent:
    def test_llm_agent_returns_true(self):
        agent = LlmAgent(name="test", model="test-llm-model")
        assert agent._is_llm_agent(agent) is True

    def test_base_agent_returns_false(self):
        class SimpleAgent(BaseAgent):
            async def _run_async_impl(self, ctx):
                yield

        agent = LlmAgent(name="test", model="test-llm-model")
        simple = SimpleAgent(name="simple")
        assert agent._is_llm_agent(simple) is False


# ---------------------------------------------------------------------------
# _resolve_model
# ---------------------------------------------------------------------------


class TestResolveModel:
    def test_resolve_model_instance(self, invocation_context):
        model = MockLLMModel(model_name="test-llm-model")
        agent = LlmAgent(name="test", model=model)

        async def run():
            return await agent._resolve_model(invocation_context)

        resolved = asyncio.run(run())
        assert resolved is model

    def test_resolve_model_string(self, invocation_context):
        agent = LlmAgent(name="test", model="test-llm-model")
        agent.model = "test-llm-model"

        async def run():
            return await agent._resolve_model(invocation_context)

        resolved = asyncio.run(run())
        assert isinstance(resolved, LLMModel)

    def test_resolve_model_factory(self, invocation_context):
        model = MockLLMModel(model_name="test-llm-model")

        async def factory(custom_data):
            return model

        agent = LlmAgent(name="test", model=factory)

        async def run():
            return await agent._resolve_model(invocation_context)

        resolved = asyncio.run(run())
        assert resolved is model


# ---------------------------------------------------------------------------
# _create_error_event
# ---------------------------------------------------------------------------


class TestCreateErrorEvent:
    def test_creates_error_event(self, invocation_context):
        agent = invocation_context.agent
        event = agent._create_error_event(invocation_context, "test_error", "Something failed")
        assert event.error_code == "test_error"
        assert event.error_message == "Something failed"
        assert event.author == agent.name
        assert event.invocation_id == invocation_context.invocation_id


# ---------------------------------------------------------------------------
# _save_output_to_state
# ---------------------------------------------------------------------------


class TestSaveOutputToState:
    def test_saves_when_output_key_set(self, invocation_context):
        agent = LlmAgent(name="test", model="test-llm-model", output_key="result")
        invocation_context.agent = agent
        event = Event(
            invocation_id="inv-1",
            author="test",
            content=Content(parts=[Part(text="hello world")]),
        )
        agent._save_output_to_state(invocation_context, event)
        assert invocation_context.session.state.get("result") == "hello world"

    def test_skips_when_no_output_key(self, invocation_context):
        agent = LlmAgent(name="test", model="test-llm-model")
        invocation_context.agent = agent
        event = Event(
            invocation_id="inv-1",
            author="test",
            content=Content(parts=[Part(text="hello")]),
        )
        agent._save_output_to_state(invocation_context, event)
        # No key set so state should be unchanged
        assert "result" not in invocation_context.session.state


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


class TestGenerateContentConfigValidator:
    def test_none_config_becomes_default(self):
        agent = LlmAgent(name="test", model="test-llm-model", generate_content_config=None)
        assert isinstance(agent.generate_content_config, GenerateContentConfig)

    def test_thinking_config_raises(self):
        from google.genai.types import ThinkingConfig

        with pytest.raises(ValueError, match="Thinking config"):
            LlmAgent(
                name="test",
                model="test-llm-model",
                generate_content_config=GenerateContentConfig(
                    thinking_config=ThinkingConfig(thinking_budget=100)
                ),
            )

    def test_tools_in_config_raises(self):
        from google.genai.types import Tool

        with pytest.raises(ValueError, match="tools must be set"):
            LlmAgent(
                name="test",
                model="test-llm-model",
                generate_content_config=GenerateContentConfig(tools=[Tool()]),
            )

    def test_system_instruction_raises(self):
        with pytest.raises(ValueError, match="System instruction"):
            LlmAgent(
                name="test",
                model="test-llm-model",
                generate_content_config=GenerateContentConfig(
                    system_instruction="bad"
                ),
            )
