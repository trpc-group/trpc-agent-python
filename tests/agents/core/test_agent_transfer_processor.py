# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for AgentTransferProcessor."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List
from unittest.mock import Mock, patch

import pytest

from trpc_agent_sdk.agents._llm_agent import LlmAgent
from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.agents.core._agent_transfer_processor import (
    AgentTransferProcessor,
    default_agent_transfer_processor,
)
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events import Event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import GenerateContentConfig


class MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-transfer-proc-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=None)

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    original_registry = ModelRegistry._registry.copy()
    ModelRegistry.register(MockLLMModel)
    yield
    ModelRegistry._registry = original_registry


@pytest.fixture
def processor():
    return AgentTransferProcessor()


@pytest.fixture
def invocation_context():
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test", user_id="u1", session_id="s1")
    )
    agent = LlmAgent(name="test_agent", model="test-transfer-proc-model")
    ctx = InvocationContext(
        session_service=service,
        invocation_id="inv-1",
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
    )
    return ctx


# ---------------------------------------------------------------------------
# _get_transfer_targets
# ---------------------------------------------------------------------------


class TestGetTransferTargets:
    def test_no_targets_when_no_sub_agents_and_no_parent(self, processor):
        agent = LlmAgent(name="solo", model="test-transfer-proc-model")
        targets = processor._get_transfer_targets(agent)
        assert targets == []

    def test_sub_agents_as_targets(self, processor):
        child = LlmAgent(name="child", model="test-transfer-proc-model")
        parent = LlmAgent(name="parent", model="test-transfer-proc-model", sub_agents=[child])
        targets = processor._get_transfer_targets(parent)
        assert child in targets

    def test_parent_as_target_when_allowed(self, processor):
        child = LlmAgent(name="child", model="test-transfer-proc-model")
        parent = LlmAgent(name="parent", model="test-transfer-proc-model", sub_agents=[child])
        targets = processor._get_transfer_targets(child)
        assert parent in targets

    def test_parent_excluded_when_disallowed(self, processor):
        child = LlmAgent(
            name="child",
            model="test-transfer-proc-model",
            disallow_transfer_to_parent=True,
        )
        parent = LlmAgent(name="parent", model="test-transfer-proc-model", sub_agents=[child])
        targets = processor._get_transfer_targets(child)
        parent_in_targets = any(t.name == "parent" for t in targets)
        assert not parent_in_targets

    def test_peer_agents_as_targets(self, processor):
        child1 = LlmAgent(name="child1", model="test-transfer-proc-model")
        child2 = LlmAgent(name="child2", model="test-transfer-proc-model")
        parent = LlmAgent(
            name="parent",
            model="test-transfer-proc-model",
            sub_agents=[child1, child2],
        )
        targets = processor._get_transfer_targets(child1)
        peer_names = [t.name for t in targets]
        assert "child2" in peer_names

    def test_peers_excluded_when_disallowed(self, processor):
        child1 = LlmAgent(
            name="child1",
            model="test-transfer-proc-model",
            disallow_transfer_to_peers=True,
        )
        child2 = LlmAgent(name="child2", model="test-transfer-proc-model")
        parent = LlmAgent(
            name="parent",
            model="test-transfer-proc-model",
            sub_agents=[child1, child2],
        )
        targets = processor._get_transfer_targets(child1)
        peer_names = [t.name for t in targets]
        assert "child2" not in peer_names


# ---------------------------------------------------------------------------
# _build_transfer_instructions
# ---------------------------------------------------------------------------


class TestBuildTransferInstructions:
    def test_empty_targets_returns_empty(self, processor):
        agent = LlmAgent(name="test", model="test-transfer-proc-model")
        result = processor._build_transfer_instructions(agent, [])
        assert result == ""

    def test_includes_target_names(self, processor):
        child = LlmAgent(name="child_agent", model="test-transfer-proc-model", description="A child")
        parent = LlmAgent(name="parent", model="test-transfer-proc-model", sub_agents=[child])
        result = processor._build_transfer_instructions(parent, [child])
        assert "child_agent" in result
        assert "A child" in result

    def test_custom_default_transfer_message(self, processor):
        child = LlmAgent(name="child", model="test-transfer-proc-model")
        agent = LlmAgent(
            name="parent",
            model="test-transfer-proc-model",
            sub_agents=[child],
            default_transfer_message="Custom transfer message",
        )
        result = processor._build_transfer_instructions(agent, [child])
        assert result == "Custom transfer message"

    def test_empty_default_transfer_message(self, processor):
        child = LlmAgent(name="child", model="test-transfer-proc-model")
        agent = LlmAgent(
            name="parent",
            model="test-transfer-proc-model",
            sub_agents=[child],
            default_transfer_message="",
        )
        result = processor._build_transfer_instructions(agent, [child])
        assert result == ""

    def test_includes_transfer_to_agent_instruction(self, processor):
        child = LlmAgent(name="child", model="test-transfer-proc-model")
        parent = LlmAgent(name="parent", model="test-transfer-proc-model", sub_agents=[child])
        result = processor._build_transfer_instructions(parent, [child])
        assert "transfer_to_agent" in result


# ---------------------------------------------------------------------------
# process_agent_transfer
# ---------------------------------------------------------------------------


class TestProcessAgentTransfer:
    def test_no_targets_returns_none(self, processor, invocation_context):
        agent = LlmAgent(name="solo", model="test-transfer-proc-model")
        invocation_context.agent = agent
        request = LlmRequest(config=GenerateContentConfig())

        async def run():
            return await processor.process_agent_transfer(request, agent, invocation_context)

        result = asyncio.run(run())
        assert result is None

    def test_adds_instructions_to_request(self, processor, invocation_context):
        child = LlmAgent(name="child", model="test-transfer-proc-model", description="Helper")
        parent = LlmAgent(
            name="parent",
            model="test-transfer-proc-model",
            sub_agents=[child],
        )
        invocation_context.agent = parent
        request = LlmRequest(config=GenerateContentConfig())

        async def run():
            return await processor.process_agent_transfer(request, parent, invocation_context)

        result = asyncio.run(run())
        assert result is None
        assert "child" in request.config.system_instruction

    def test_appends_to_existing_instructions(self, processor, invocation_context):
        child = LlmAgent(name="child", model="test-transfer-proc-model", description="Helper")
        parent = LlmAgent(
            name="parent",
            model="test-transfer-proc-model",
            sub_agents=[child],
        )
        invocation_context.agent = parent
        request = LlmRequest(config=GenerateContentConfig(system_instruction="Existing instruction"))

        async def run():
            return await processor.process_agent_transfer(request, parent, invocation_context)

        result = asyncio.run(run())
        assert result is None
        assert "Existing instruction" in request.config.system_instruction
        assert "child" in request.config.system_instruction


# ---------------------------------------------------------------------------
# _build_target_agent_info
# ---------------------------------------------------------------------------


class TestBuildTargetAgentInfo:
    def test_includes_name_and_description(self, processor):
        agent = Mock()
        agent.name = "my_agent"
        agent.description = "My agent description"
        info = processor._build_target_agent_info(agent)
        assert "my_agent" in info
        assert "My agent description" in info


# ---------------------------------------------------------------------------
# default instance
# ---------------------------------------------------------------------------


class TestDefaultInstance:
    def test_default_processor_exists(self):
        assert default_agent_transfer_processor is not None
        assert isinstance(default_agent_transfer_processor, AgentTransferProcessor)
