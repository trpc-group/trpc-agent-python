# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for trpc_agent_sdk.context._common."""

from __future__ import annotations

import contextvars

import pytest

from trpc_agent_sdk.context._agent_context import AgentContext
from trpc_agent_sdk.context._common import (
    create_agent_context,
    get_data_by_agent_ctx,
    get_invocation_ctx,
    invocation_ctx,
    pop_data_by_agent_ctx,
    reset_invocation_ctx,
    set_data_to_agent_ctx,
    set_invocation_ctx,
)
from trpc_agent_sdk.context._invocation_context import InvocationContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent_ctx(**metadata) -> AgentContext:
    ctx = AgentContext()
    for k, v in metadata.items():
        ctx.with_metadata(k, v)
    return ctx


# ---------------------------------------------------------------------------
# get_data_by_agent_ctx
# ---------------------------------------------------------------------------


class TestGetDataByAgentCtx:
    def test_returns_existing_value(self):
        ctx = _make_agent_ctx(key="value")
        assert get_data_by_agent_ctx(ctx, "key") == "value"

    def test_returns_none_for_missing_key(self):
        ctx = _make_agent_ctx()
        assert get_data_by_agent_ctx(ctx, "missing") is None

    def test_returns_custom_default_for_missing_key(self):
        ctx = _make_agent_ctx()
        assert get_data_by_agent_ctx(ctx, "missing", "default_val") == "default_val"

    def test_returns_none_value_when_stored(self):
        ctx = _make_agent_ctx(key=None)
        assert get_data_by_agent_ctx(ctx, "key") is None


# ---------------------------------------------------------------------------
# pop_data_by_agent_ctx
# ---------------------------------------------------------------------------


class TestPopDataByAgentCtx:
    def test_pops_existing_value(self):
        ctx = _make_agent_ctx(key="value")
        result = pop_data_by_agent_ctx(ctx, "key")
        assert result == "value"

    def test_pop_removes_key(self):
        ctx = _make_agent_ctx(key="value")
        pop_data_by_agent_ctx(ctx, "key")
        assert get_data_by_agent_ctx(ctx, "key") is None

    def test_pop_missing_key_returns_none(self):
        ctx = _make_agent_ctx()
        assert pop_data_by_agent_ctx(ctx, "missing") is None

    def test_pop_missing_key_returns_custom_default(self):
        ctx = _make_agent_ctx()
        assert pop_data_by_agent_ctx(ctx, "missing", "fb") == "fb"


# ---------------------------------------------------------------------------
# set_data_to_agent_ctx
# ---------------------------------------------------------------------------


class TestSetDataToAgentCtx:
    def test_sets_value(self):
        ctx = _make_agent_ctx()
        set_data_to_agent_ctx(ctx, "key", 123)
        assert get_data_by_agent_ctx(ctx, "key") == 123

    def test_overwrites_existing_value(self):
        ctx = _make_agent_ctx(key="old")
        set_data_to_agent_ctx(ctx, "key", "new")
        assert get_data_by_agent_ctx(ctx, "key") == "new"


# ---------------------------------------------------------------------------
# invocation context var (set / get / reset)
# ---------------------------------------------------------------------------


class TestInvocationContextVar:
    def test_default_is_none(self):
        ctx = contextvars.copy_context()
        value = ctx.run(get_invocation_ctx)
        assert value is None

    def test_set_and_get(self):
        def _inner():
            from unittest.mock import Mock
            mock_ctx = Mock(spec=InvocationContext)
            token = set_invocation_ctx(mock_ctx)
            assert get_invocation_ctx() is mock_ctx
            reset_invocation_ctx(token)

        ctx = contextvars.copy_context()
        ctx.run(_inner)

    def test_reset_restores_previous(self):
        def _inner():
            from unittest.mock import Mock
            original = get_invocation_ctx()
            mock_ctx = Mock(spec=InvocationContext)
            token = set_invocation_ctx(mock_ctx)
            reset_invocation_ctx(token)
            assert get_invocation_ctx() is original

        ctx = contextvars.copy_context()
        ctx.run(_inner)

    def test_reset_with_wrong_var_token_does_not_raise(self):
        """reset_invocation_ctx catches ValueError (e.g. token from a different ContextVar)."""
        def _inner():
            other_var: contextvars.ContextVar = contextvars.ContextVar("other")
            other_token = other_var.set("x")
            result = reset_invocation_ctx(other_token)
            assert result is None

        ctx = contextvars.copy_context()
        ctx.run(_inner)

    def test_nested_set_and_reset(self):
        def _inner():
            from unittest.mock import Mock
            ctx1 = Mock(spec=InvocationContext)
            ctx2 = Mock(spec=InvocationContext)
            token1 = set_invocation_ctx(ctx1)
            assert get_invocation_ctx() is ctx1
            token2 = set_invocation_ctx(ctx2)
            assert get_invocation_ctx() is ctx2
            reset_invocation_ctx(token2)
            assert get_invocation_ctx() is ctx1
            reset_invocation_ctx(token1)

        ctx = contextvars.copy_context()
        ctx.run(_inner)

    def test_context_var_is_named_correctly(self):
        assert invocation_ctx.name == "invocation_ctx"


# ---------------------------------------------------------------------------
# create_agent_context
# ---------------------------------------------------------------------------


class TestCreateAgentContext:
    def test_returns_agent_context_instance(self):
        ctx = create_agent_context()
        assert isinstance(ctx, AgentContext)

    def test_returns_fresh_instance_each_call(self):
        ctx1 = create_agent_context()
        ctx2 = create_agent_context()
        assert ctx1 is not ctx2

    def test_default_values(self):
        ctx = create_agent_context()
        assert ctx.trpc_ctx is None
        assert ctx.timeout == 3000
        assert ctx.metadata == {}
