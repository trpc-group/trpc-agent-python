# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.context._agent_context."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from trpc_agent_sdk.context._agent_context import AgentContext, new_agent_context


class TestAgentContextDefaults:
    def test_default_trpc_ctx_is_none(self):
        ctx = AgentContext()
        assert ctx.trpc_ctx is None

    def test_default_timeout(self):
        ctx = AgentContext()
        assert ctx.timeout == 3000

    def test_default_metadata_is_empty_dict(self):
        ctx = AgentContext()
        assert ctx.metadata == {}


class TestAgentContextTrpcCtx:
    def test_set_trpc_ctx(self):
        ctx = AgentContext(trpc_ctx="some_ctx")
        assert ctx.trpc_ctx == "some_ctx"

    def test_set_trpc_ctx_arbitrary_type(self):
        obj = object()
        ctx = AgentContext(trpc_ctx=obj)
        assert ctx.trpc_ctx is obj


class TestAgentContextTimeout:
    def test_set_timeout(self):
        ctx = AgentContext()
        ctx.set_timeout(5000)
        assert ctx.timeout == 5000

    def test_set_timeout_to_zero(self):
        ctx = AgentContext()
        ctx.set_timeout(0)
        assert ctx.timeout == 0


class TestAgentContextMetadata:
    def test_with_metadata_adds_entry(self):
        ctx = AgentContext()
        ctx.with_metadata("key1", "value1")
        assert ctx.metadata == {"key1": "value1"}

    def test_with_metadata_overwrites(self):
        ctx = AgentContext()
        ctx.with_metadata("key1", "v1")
        ctx.with_metadata("key1", "v2")
        assert ctx.metadata["key1"] == "v2"

    def test_get_metadata_existing_key(self):
        ctx = AgentContext()
        ctx.with_metadata("key1", 42)
        assert ctx.get_metadata("key1") == 42

    def test_get_metadata_missing_key_returns_none(self):
        ctx = AgentContext()
        assert ctx.get_metadata("nonexistent") is None

    def test_get_metadata_missing_key_returns_custom_default(self):
        ctx = AgentContext()
        assert ctx.get_metadata("nonexistent", "fallback") == "fallback"

    def test_metadata_multiple_entries(self):
        ctx = AgentContext()
        ctx.with_metadata("a", 1)
        ctx.with_metadata("b", 2)
        ctx.with_metadata("c", 3)
        assert ctx.metadata == {"a": 1, "b": 2, "c": 3}

    def test_metadata_is_mutable_reference(self):
        ctx = AgentContext()
        ctx.with_metadata("key", [1, 2])
        ctx.metadata["key"].append(3)
        assert ctx.get_metadata("key") == [1, 2, 3]


class TestAgentContextPydanticConfig:
    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AgentContext(unknown_field="x")

    def test_model_dump(self):
        ctx = AgentContext(trpc_ctx="ctx_val")
        data = ctx.model_dump()
        assert data["trpc_ctx"] == "ctx_val"
        assert "_timeout" not in data
        assert "_metadata" not in data


class TestNewAgentContext:
    def test_default_creation(self):
        ctx = new_agent_context()
        assert isinstance(ctx, AgentContext)
        assert ctx.timeout == 3000
        assert ctx.metadata == {}

    def test_custom_timeout(self):
        ctx = new_agent_context(timeout=10000)
        assert ctx.timeout == 10000

    def test_with_metadata(self):
        ctx = new_agent_context(metadata={"a": 1, "b": "hello"})
        assert ctx.get_metadata("a") == 1
        assert ctx.get_metadata("b") == "hello"

    def test_with_none_metadata(self):
        ctx = new_agent_context(metadata=None)
        assert ctx.metadata == {}

    def test_with_empty_metadata(self):
        ctx = new_agent_context(metadata={})
        assert ctx.metadata == {}

    def test_custom_timeout_and_metadata(self):
        ctx = new_agent_context(timeout=500, metadata={"x": True})
        assert ctx.timeout == 500
        assert ctx.get_metadata("x") is True
