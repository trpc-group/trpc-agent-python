# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for event utility functions."""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, List
from unittest.mock import Mock

import pytest

from trpc_agent_sdk.agents._base_agent import BaseAgent
from trpc_agent_sdk.context import InvocationContext, create_agent_context
from trpc_agent_sdk.events._event import Event
from trpc_agent_sdk.events._utils import create_text_event
from trpc_agent_sdk.models import LLMModel, LlmRequest, LlmResponse, ModelRegistry
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content, Part


# ---------------------------------------------------------------------------
# Stubs for agent / model registration
# ---------------------------------------------------------------------------


class _StubAgent(BaseAgent):
    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        return
        yield


class _MockLLMModel(LLMModel):
    @classmethod
    def supported_models(cls) -> List[str]:
        return [r"test-utils-.*"]

    async def _generate_async_impl(self, request, stream=False, ctx=None):
        yield LlmResponse(content=None)

    def validate_request(self, request):
        pass


@pytest.fixture(scope="module", autouse=True)
def register_test_model():
    original_registry = ModelRegistry._registry.copy()
    ModelRegistry.register(_MockLLMModel)
    yield
    ModelRegistry._registry = original_registry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    invocation_id: str = "inv-1",
    agent_name: str = "test_agent",
    branch: str = None,
) -> InvocationContext:
    agent = _StubAgent(name=agent_name)
    service = InMemorySessionService()
    session = asyncio.run(
        service.create_session(app_name="test_app", user_id="user-1", session_id="s-1")
    )
    return InvocationContext(
        session_service=service,
        invocation_id=invocation_id,
        agent=agent,
        agent_context=create_agent_context(),
        session=session,
        branch=branch,
    )


# ---------------------------------------------------------------------------
# create_text_event basic behavior
# ---------------------------------------------------------------------------


class TestCreateTextEvent:
    def test_basic_text_event(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "hello world")

        assert isinstance(event, Event)
        assert event.invocation_id == "inv-1"
        assert event.author == "test_agent"
        assert event.get_text() == "hello world"
        assert event.visible is True
        assert event.partial is None or event.partial is False

    def test_text_content_in_parts(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "hello")

        assert event.content is not None
        assert event.content.parts is not None
        assert len(event.content.parts) == 1
        assert event.content.parts[0].text == "hello"

    def test_content_role_is_model(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "test")
        assert event.content.role == "model"

    def test_author_from_context(self):
        ctx = _make_ctx(agent_name="my_agent")
        event = create_text_event(ctx, "text")
        assert event.author == "my_agent"

    def test_invocation_id_from_context(self):
        ctx = _make_ctx(invocation_id="custom-inv")
        event = create_text_event(ctx, "text")
        assert event.invocation_id == "custom-inv"


# ---------------------------------------------------------------------------
# branch handling
# ---------------------------------------------------------------------------


class TestCreateTextEventBranch:
    def test_branch_from_context(self):
        ctx = _make_ctx(branch="root.child")
        event = create_text_event(ctx, "text")
        assert event.branch == "root.child"

    def test_no_branch(self):
        ctx = _make_ctx(branch=None)
        event = create_text_event(ctx, "text")
        assert event.branch is None


# ---------------------------------------------------------------------------
# visible parameter
# ---------------------------------------------------------------------------


class TestCreateTextEventVisible:
    def test_visible_true_by_default(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "text")
        assert event.visible is True

    def test_visible_false(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "text", visible=False)
        assert event.visible is False

    def test_visible_true_explicit(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "text", visible=True)
        assert event.visible is True


# ---------------------------------------------------------------------------
# save parameter (maps to partial)
# ---------------------------------------------------------------------------


class TestCreateTextEventSave:
    def test_save_false_by_default(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "text")
        assert event.partial is False or event.partial is None

    def test_save_true_sets_partial(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "text", save=True)
        assert event.partial is True

    def test_save_false_sets_partial_false(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "text", save=False)
        assert event.partial is False or event.partial is None


# ---------------------------------------------------------------------------
# thought_text parameter
# ---------------------------------------------------------------------------


class TestCreateTextEventThought:
    def test_no_thought_text(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "main text")
        assert len(event.content.parts) == 1
        assert event.content.parts[0].text == "main text"

    def test_with_thought_text(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "main text", thought_text="thinking...")

        assert len(event.content.parts) == 2
        thought_part = event.content.parts[0]
        text_part = event.content.parts[1]
        assert thought_part.text == "thinking..."
        assert thought_part.thought is True
        assert text_part.text == "main text"

    def test_thought_text_none(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "text", thought_text=None)
        assert len(event.content.parts) == 1

    def test_get_text_includes_both(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "main", thought_text="thought")
        assert "thought" in event.get_text()
        assert "main" in event.get_text()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestCreateTextEventEdgeCases:
    def test_empty_text(self):
        ctx = _make_ctx()
        event = create_text_event(ctx, "")
        assert event.content.parts[-1].text == ""

    def test_long_text(self):
        ctx = _make_ctx()
        long_text = "x" * 10000
        event = create_text_event(ctx, long_text)
        assert event.get_text().endswith("x" * 100)

    def test_text_with_special_chars(self):
        ctx = _make_ctx()
        special = "Hello\n\tWorld! 你好 🌍 <tag>&amp;</tag>"
        event = create_text_event(ctx, special)
        assert special in event.get_text()

    def test_combined_params(self):
        ctx = _make_ctx(invocation_id="inv-99", agent_name="combo_agent", branch="b1.b2")
        event = create_text_event(
            ctx,
            "response",
            thought_text="reasoning",
            visible=False,
            save=True,
        )
        assert event.invocation_id == "inv-99"
        assert event.author == "combo_agent"
        assert event.branch == "b1.b2"
        assert event.visible is False
        assert event.partial is True
        assert len(event.content.parts) == 2
