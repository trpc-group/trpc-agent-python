# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools._set_model_response_tool import SetModelResponseTool
from trpc_agent_sdk.types import FunctionDeclaration


class OutputSchema(BaseModel):
    answer: str
    score: float


class EmptySchema(BaseModel):
    pass


class ComplexSchema(BaseModel):
    name: str
    tags: list[str]
    count: int = 0


class TestSetModelResponseToolInit:

    def test_init(self):
        tool = SetModelResponseTool(output_schema=OutputSchema)
        assert tool.name == "set_model_response"
        assert "final response" in tool.description.lower() or "final structured" in tool.description.lower()
        assert tool.output_schema is OutputSchema

    def test_init_creates_local_func(self):
        tool = SetModelResponseTool(output_schema=OutputSchema)
        assert callable(tool.func)
        assert tool.func() == "Response set successfully."

    def test_init_with_empty_schema(self):
        tool = SetModelResponseTool(output_schema=EmptySchema)
        assert tool.name == "set_model_response"

    def test_each_instance_gets_own_func(self):
        tool1 = SetModelResponseTool(output_schema=OutputSchema)
        tool2 = SetModelResponseTool(output_schema=ComplexSchema)
        assert tool1.func is not tool2.func


class TestSetModelResponseToolGetDeclaration:

    def test_get_declaration(self):
        tool = SetModelResponseTool(output_schema=OutputSchema)
        decl = tool._get_declaration()
        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == "set_model_response"

    def test_declaration_has_schema_fields(self):
        tool = SetModelResponseTool(output_schema=OutputSchema)
        decl = tool._get_declaration()
        assert decl.parameters is not None
        assert decl.parameters.properties is not None
        assert "answer" in decl.parameters.properties
        assert "score" in decl.parameters.properties


class TestSetModelResponseToolRunAsync:

    @pytest.mark.asyncio
    async def test_run_validates_and_returns(self):
        tool = SetModelResponseTool(output_schema=OutputSchema)
        ctx = MagicMock(spec=InvocationContext)

        result = await tool._run_async_impl(
            args={"answer": "hello", "score": 0.95},
            tool_context=ctx,
        )
        assert result == {"answer": "hello", "score": 0.95}

    @pytest.mark.asyncio
    async def test_run_with_complex_schema(self):
        tool = SetModelResponseTool(output_schema=ComplexSchema)
        ctx = MagicMock(spec=InvocationContext)

        result = await tool._run_async_impl(
            args={"name": "test", "tags": ["a", "b"], "count": 5},
            tool_context=ctx,
        )
        assert result == {"name": "test", "tags": ["a", "b"], "count": 5}

    @pytest.mark.asyncio
    async def test_run_with_default_values(self):
        tool = SetModelResponseTool(output_schema=ComplexSchema)
        ctx = MagicMock(spec=InvocationContext)

        result = await tool._run_async_impl(
            args={"name": "test", "tags": []},
            tool_context=ctx,
        )
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_run_with_invalid_args_raises(self):
        tool = SetModelResponseTool(output_schema=OutputSchema)
        ctx = MagicMock(spec=InvocationContext)

        with pytest.raises(Exception):
            await tool._run_async_impl(
                args={"answer": "hello"},  # Missing 'score'
                tool_context=ctx,
            )
