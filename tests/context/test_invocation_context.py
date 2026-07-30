# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.context._invocation_context."""

from __future__ import annotations

from types import MappingProxyType
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from trpc_agent_sdk.abc import (
    AgentABC,
    ArtifactEntry,
    ArtifactId,
    ArtifactServiceABC,
    MemoryServiceABC,
    SessionABC,
    SessionServiceABC,
)
from trpc_agent_sdk.context._agent_context import AgentContext
from trpc_agent_sdk.context._invocation_context import (
    InvocationContext,
    new_invocation_context_id,
)
from trpc_agent_sdk.types import EventActions, SearchMemoryResponse, State


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_session():
    session = Mock(spec=SessionABC)
    session.app_name = "test_app"
    session.user_id = "user_1"
    session.id = "session_1"
    session.state = {"existing_key": "existing_value"}
    session.save_key = "test_app/user_1/session_1"
    return session


@pytest.fixture
def mock_agent():
    agent = Mock(spec=AgentABC)
    agent.name = "test_agent"
    return agent


@pytest.fixture
def mock_session_service():
    return AsyncMock(spec=SessionServiceABC)


@pytest.fixture
def mock_artifact_service():
    return AsyncMock(spec=ArtifactServiceABC)


@pytest.fixture
def mock_memory_service():
    return AsyncMock(spec=MemoryServiceABC)


@pytest.fixture
def agent_context():
    return AgentContext()


@pytest.fixture
def invocation_context(mock_session_service, mock_session, mock_agent, agent_context):
    return InvocationContext(
        session_service=mock_session_service,
        invocation_id="inv-test-001",
        agent=mock_agent,
        agent_context=agent_context,
        session=mock_session,
    )


@pytest.fixture
def full_context(
    mock_session_service,
    mock_session,
    mock_agent,
    agent_context,
    mock_artifact_service,
    mock_memory_service,
):
    return InvocationContext(
        session_service=mock_session_service,
        artifact_service=mock_artifact_service,
        memory_service=mock_memory_service,
        invocation_id="inv-full-001",
        agent=mock_agent,
        agent_context=agent_context,
        session=mock_session,
    )


# ---------------------------------------------------------------------------
# Construction & defaults
# ---------------------------------------------------------------------------


class TestInvocationContextCreation:
    def test_required_fields(self, invocation_context):
        assert invocation_context.invocation_id == "inv-test-001"
        assert invocation_context.session_service is not None
        assert invocation_context.agent is not None
        assert invocation_context.session is not None
        assert invocation_context.agent_context is not None

    def test_default_optional_fields(self, invocation_context):
        assert invocation_context.artifact_service is None
        assert invocation_context.memory_service is None
        assert invocation_context.branch is None
        assert invocation_context.user_content is None
        assert invocation_context.end_invocation is False
        assert invocation_context.run_config is None
        assert invocation_context.callback_state is None
        assert invocation_context.active_streaming_tools is None
        assert invocation_context.function_call_id is None
        assert invocation_context.override_messages is None
        assert invocation_context.session_key is None

    def test_event_actions_has_default(self, invocation_context):
        assert isinstance(invocation_context.event_actions, EventActions)

    def test_extra_fields_forbidden(self, mock_session_service, mock_session, mock_agent, agent_context):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            InvocationContext(
                session_service=mock_session_service,
                invocation_id="inv-1",
                agent=mock_agent,
                agent_context=agent_context,
                session=mock_session,
                bogus_field="nope",
            )


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


class TestInvocationContextProperties:
    def test_app_name(self, invocation_context, mock_session):
        assert invocation_context.app_name == mock_session.app_name

    def test_user_id(self, invocation_context, mock_session):
        assert invocation_context.user_id == mock_session.user_id

    def test_session_id(self, invocation_context, mock_session):
        assert invocation_context.session_id == mock_session.id

    def test_agent_name(self, invocation_context, mock_agent):
        assert invocation_context.agent_name == mock_agent.name

    def test_session_state_returns_mapping_proxy(self, invocation_context):
        state = invocation_context.session_state
        assert isinstance(state, MappingProxyType)

    def test_session_state_is_immutable(self, invocation_context):
        state = invocation_context.session_state
        with pytest.raises(TypeError):
            state["new_key"] = "fail"

    def test_session_state_reflects_session_data(self, invocation_context, mock_session):
        assert invocation_context.session_state["existing_key"] == "existing_value"

    def test_actions_returns_event_actions(self, invocation_context):
        assert invocation_context.actions is invocation_context.event_actions


class TestInvocationContextState:
    def test_state_creates_state_object(self, invocation_context):
        state = invocation_context.state
        assert isinstance(state, State)

    def test_state_is_cached(self, invocation_context):
        s1 = invocation_context.state
        s2 = invocation_context.state
        assert s1 is s2

    def test_state_uses_session_state_and_delta(self, invocation_context, mock_session):
        state = invocation_context.state
        assert state.get("existing_key") == "existing_value"

    def test_state_delta_writes_to_event_actions(self, invocation_context):
        state = invocation_context.state
        state["new_key"] = "new_value"
        assert invocation_context.event_actions.state_delta["new_key"] == "new_value"


class TestInvocationContextBranch:
    def test_branch_can_be_set(self, mock_session_service, mock_session, mock_agent, agent_context):
        ctx = InvocationContext(
            session_service=mock_session_service,
            invocation_id="inv-1",
            agent=mock_agent,
            agent_context=agent_context,
            session=mock_session,
            branch="root.child",
        )
        assert ctx.branch == "root.child"


# ---------------------------------------------------------------------------
# Artifact methods
# ---------------------------------------------------------------------------


class TestLoadArtifact:
    @pytest.mark.asyncio
    async def test_raises_when_no_service(self, invocation_context):
        with pytest.raises(ValueError, match="Artifact service is not initialized"):
            await invocation_context.load_artifact("file.txt")

    @pytest.mark.asyncio
    async def test_calls_service(self, full_context, mock_artifact_service):
        mock_entry = Mock(spec=ArtifactEntry)
        mock_artifact_service.load_artifact.return_value = mock_entry

        result = await full_context.load_artifact("file.txt", version=2)

        assert result is mock_entry
        mock_artifact_service.load_artifact.assert_awaited_once()
        call_kwargs = mock_artifact_service.load_artifact.call_args
        artifact_id = call_kwargs.kwargs["artifact_id"]
        assert artifact_id.app_name == "test_app"
        assert artifact_id.user_id == "user_1"
        assert artifact_id.session_id == "session_1"
        assert artifact_id.filename == "file.txt"
        assert call_kwargs.kwargs["version"] == 2

    @pytest.mark.asyncio
    async def test_default_version_none(self, full_context, mock_artifact_service):
        mock_artifact_service.load_artifact.return_value = None
        await full_context.load_artifact("doc.pdf")
        call_kwargs = mock_artifact_service.load_artifact.call_args
        assert call_kwargs.kwargs["version"] is None


class TestSaveArtifact:
    @pytest.mark.asyncio
    async def test_raises_when_no_service(self, invocation_context):
        with pytest.raises(ValueError, match="Artifact service is not initialized"):
            await invocation_context.save_artifact("file.txt", Mock())

    @pytest.mark.asyncio
    async def test_saves_and_returns_version(self, full_context, mock_artifact_service):
        mock_artifact_service.save_artifact.return_value = 3
        mock_part = Mock()

        version = await full_context.save_artifact("output.txt", mock_part)

        assert version == 3
        mock_artifact_service.save_artifact.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_updates_artifact_delta(self, full_context, mock_artifact_service):
        mock_artifact_service.save_artifact.return_value = 5

        await full_context.save_artifact("result.bin", Mock())

        assert full_context.event_actions.artifact_delta["result.bin"] == 5

    @pytest.mark.asyncio
    async def test_artifact_id_fields(self, full_context, mock_artifact_service):
        mock_artifact_service.save_artifact.return_value = 1
        await full_context.save_artifact("data.csv", Mock())

        call_kwargs = mock_artifact_service.save_artifact.call_args
        artifact_id = call_kwargs.kwargs["artifact_id"]
        assert artifact_id.app_name == "test_app"
        assert artifact_id.user_id == "user_1"
        assert artifact_id.session_id == "session_1"
        assert artifact_id.filename == "data.csv"


class TestListArtifacts:
    @pytest.mark.asyncio
    async def test_raises_when_no_service(self, invocation_context):
        with pytest.raises(ValueError, match="Artifact service is not initialized"):
            await invocation_context.list_artifacts()

    @pytest.mark.asyncio
    async def test_returns_list(self, full_context, mock_artifact_service):
        mock_artifact_service.list_artifact_keys.return_value = ["a.txt", "b.txt"]

        result = await full_context.list_artifacts()

        assert result == ["a.txt", "b.txt"]

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, full_context, mock_artifact_service):
        mock_artifact_service.list_artifact_keys.return_value = []
        result = await full_context.list_artifacts()
        assert result == []


# ---------------------------------------------------------------------------
# Memory methods
# ---------------------------------------------------------------------------


class TestSearchMemory:
    @pytest.mark.asyncio
    async def test_raises_when_no_service(self, invocation_context):
        with pytest.raises(ValueError, match="Memory service is not available"):
            await invocation_context.search_memory("test query")

    @pytest.mark.asyncio
    async def test_calls_service(self, full_context, mock_memory_service):
        mock_response = Mock(spec=SearchMemoryResponse)
        mock_memory_service.search_memory.return_value = mock_response

        result = await full_context.search_memory("find this")

        assert result is mock_response
        mock_memory_service.search_memory.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_passes_correct_key(self, full_context, mock_memory_service):
        mock_memory_service.search_memory.return_value = Mock(spec=SearchMemoryResponse)

        await full_context.search_memory("query")

        call_kwargs = mock_memory_service.search_memory.call_args
        assert call_kwargs.kwargs["key"] == "test_app/user_1"
        assert call_kwargs.kwargs["query"] == "query"
        assert call_kwargs.kwargs["agent_context"] is full_context.agent_context


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


class TestRaiseIfCancelled:
    @pytest.mark.asyncio
    async def test_no_session_key_is_noop(self, invocation_context):
        await invocation_context.raise_if_cancelled()

    @pytest.mark.asyncio
    async def test_delegates_to_cancel_module(self, invocation_context):
        invocation_context.session_key = Mock()
        with patch("trpc_agent_sdk.cancel.raise_if_cancelled", new_callable=AsyncMock) as mock_raise:
            await invocation_context.raise_if_cancelled()
            mock_raise.assert_awaited_once_with(invocation_context.session_key)


class TestGetCancelEvent:
    @pytest.mark.asyncio
    async def test_no_session_key_returns_none(self, invocation_context):
        result = await invocation_context.get_cancel_event()
        assert result is None

    @pytest.mark.asyncio
    async def test_delegates_to_cancel_module(self, invocation_context):
        import asyncio

        event = asyncio.Event()
        invocation_context.session_key = Mock()
        with patch("trpc_agent_sdk.cancel.get_cancel_event", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = event
            result = await invocation_context.get_cancel_event()
            assert result is event
            mock_get.assert_awaited_once_with(invocation_context.session_key)


# ---------------------------------------------------------------------------
# new_invocation_context_id
# ---------------------------------------------------------------------------


class TestNewInvocationContextId:
    def test_starts_with_prefix(self):
        cid = new_invocation_context_id()
        assert cid.startswith("e-")

    def test_unique_ids(self):
        ids = {new_invocation_context_id() for _ in range(100)}
        assert len(ids) == 100

    def test_format_is_e_dash_uuid(self):
        import re

        cid = new_invocation_context_id()
        pattern = r"^e-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        assert re.match(pattern, cid), f"ID {cid!r} does not match expected pattern"

    def test_return_type_is_str(self):
        assert isinstance(new_invocation_context_id(), str)
