# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.server.a2a.converters._request_converter."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from a2a.server.agent_execution.context import RequestContext
from a2a.types import Message, Part, Role, TextPart

from trpc_agent_sdk.server.a2a.converters._request_converter import (
    _get_user_id_default,
    _resolve_user_id,
    convert_a2a_cancel_request_to_run_args,
    convert_a2a_request_to_trpc_agent_run_args,
    get_user_session_id,
)


def _make_context(*, context_id="ctx-1", message=None, user_name=None):
    ctx = MagicMock(spec=RequestContext)
    ctx.context_id = context_id
    ctx.task_id = "task-1"
    if user_name:
        ctx.call_context = MagicMock()
        ctx.call_context.user = MagicMock()
        ctx.call_context.user.user_name = user_name
    else:
        ctx.call_context = None
    ctx.message = message
    return ctx


# ---------------------------------------------------------------------------
# _get_user_id_default
# ---------------------------------------------------------------------------
class TestGetUserIdDefault:
    def test_returns_user_name_from_call_context(self):
        ctx = _make_context(user_name="alice")
        assert _get_user_id_default(ctx) == "alice"

    def test_falls_back_to_context_id(self):
        ctx = _make_context(context_id="ctx-42")
        assert _get_user_id_default(ctx) == "A2A_USER_ctx-42"

    def test_falls_back_when_user_name_empty(self):
        ctx = _make_context(context_id="ctx-5")
        ctx.call_context = MagicMock()
        ctx.call_context.user = MagicMock()
        ctx.call_context.user.user_name = ""
        assert _get_user_id_default(ctx) == "A2A_USER_ctx-5"

    def test_falls_back_when_user_is_none(self):
        ctx = _make_context(context_id="ctx-6")
        ctx.call_context = MagicMock()
        ctx.call_context.user = None
        assert _get_user_id_default(ctx) == "A2A_USER_ctx-6"


# ---------------------------------------------------------------------------
# _resolve_user_id
# ---------------------------------------------------------------------------
class TestResolveUserId:
    async def test_default_when_no_extractor(self):
        ctx = _make_context(user_name="bob")
        result = await _resolve_user_id(ctx, None)
        assert result == "bob"

    async def test_sync_extractor(self):
        ctx = _make_context()
        result = await _resolve_user_id(ctx, lambda r: "custom_user")
        assert result == "custom_user"

    async def test_async_extractor(self):
        async def async_extractor(r):
            return "async_user"

        ctx = _make_context()
        result = await _resolve_user_id(ctx, async_extractor)
        assert result == "async_user"


# ---------------------------------------------------------------------------
# get_user_session_id
# ---------------------------------------------------------------------------
class TestGetUserSessionId:
    async def test_returns_tuple(self):
        ctx = _make_context(user_name="user1", context_id="session-1")
        user_id, session_id = await get_user_session_id(ctx)
        assert user_id == "user1"
        assert session_id == "session-1"

    async def test_with_custom_extractor(self):
        ctx = _make_context(context_id="s1")
        user_id, session_id = await get_user_session_id(ctx, lambda r: "ext_user")
        assert user_id == "ext_user"
        assert session_id == "s1"


# ---------------------------------------------------------------------------
# convert_a2a_request_to_trpc_agent_run_args
# ---------------------------------------------------------------------------
class TestConvertA2aRequestToRunArgs:
    async def test_basic_conversion(self):
        msg = Message(
            message_id="m1",
            role=Role.user,
            parts=[Part(root=TextPart(text="hello"))],
        )
        ctx = _make_context(user_name="alice", context_id="s1", message=msg)
        result = await convert_a2a_request_to_trpc_agent_run_args(ctx)
        assert result["user_id"] == "alice"
        assert result["session_id"] == "s1"
        assert result["new_message"].role == "user"
        assert len(result["new_message"].parts) == 1
        assert result["run_config"] is not None

    async def test_raises_on_none_message(self):
        ctx = _make_context(message=None)
        with pytest.raises(ValueError, match="Request message cannot be None"):
            await convert_a2a_request_to_trpc_agent_run_args(ctx)

    async def test_message_metadata_included(self):
        msg = Message(
            message_id="m1",
            role=Role.user,
            parts=[Part(root=TextPart(text="hi"))],
            metadata={"key": "val"},
        )
        ctx = _make_context(message=msg)
        result = await convert_a2a_request_to_trpc_agent_run_args(ctx)
        assert result["run_config"].agent_run_config["metadata"]["key"] == "val"

    async def test_message_metadata_non_dict_treated_as_empty(self):
        msg = Message(
            message_id="m1",
            role=Role.user,
            parts=[Part(root=TextPart(text="hi"))],
        )
        msg.metadata = "not_a_dict"
        ctx = _make_context(message=msg)
        result = await convert_a2a_request_to_trpc_agent_run_args(ctx)
        assert result["run_config"].agent_run_config["metadata"] == {}


# ---------------------------------------------------------------------------
# convert_a2a_cancel_request_to_run_args
# ---------------------------------------------------------------------------
class TestConvertA2aCancelRequestToRunArgs:
    async def test_returns_user_and_session(self):
        ctx = _make_context(user_name="bob", context_id="s2")
        result = await convert_a2a_cancel_request_to_run_args(ctx)
        assert result == {"user_id": "bob", "session_id": "s2"}

    async def test_with_custom_extractor(self):
        ctx = _make_context(context_id="s3")
        result = await convert_a2a_cancel_request_to_run_args(ctx, lambda r: "custom")
        assert result["user_id"] == "custom"
