# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp.types import (
    CallToolResult,
    EmbeddedResource,
    ImageContent,
    TextContent,
    TextResourceContents,
    BlobResourceContents,
    Tool as McpBaseTool,
)

from trpc_agent_sdk.tools.mcp_tool._mcp_tool import MCPTool
from trpc_agent_sdk.tools.mcp_tool._mcp_session_manager import MCPSessionManager
from trpc_agent_sdk.types import FunctionDeclaration, Schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mcp_tool(name="test_tool", description="A test tool", input_schema=None):
    return McpBaseTool(
        name=name,
        description=description,
        inputSchema=input_schema or {"type": "object", "properties": {}},
    )


def _make_session_manager():
    mgr = MagicMock(spec=MCPSessionManager)
    mgr.create_session = AsyncMock()
    return mgr


def _make_mcp_tool_instance(name="test_tool", description="A test tool", input_schema=None, **kwargs):
    mcp_tool = _make_mcp_tool(name=name, description=description, input_schema=input_schema)
    mgr = _make_session_manager()
    return MCPTool(mcp_tool=mcp_tool, mcp_session_manager=mgr, **kwargs), mgr


# ---------------------------------------------------------------------------
# Tests: __init__
# ---------------------------------------------------------------------------

class TestMCPToolInit:
    def test_basic_init(self):
        tool, mgr = _make_mcp_tool_instance()
        assert tool.name == "test_tool"
        assert tool.description == "A test tool"

    def test_none_mcp_tool_raises(self):
        mgr = _make_session_manager()
        with pytest.raises(ValueError, match="cannot be None"):
            MCPTool(mcp_tool=None, mcp_session_manager=mgr)

    def test_none_session_manager_raises(self):
        mcp_tool = _make_mcp_tool()
        with pytest.raises(ValueError, match="cannot be None"):
            MCPTool(mcp_tool=mcp_tool, mcp_session_manager=None)

    def test_empty_description_uses_empty_string(self):
        mcp_tool = McpBaseTool(
            name="t",
            description=None,
            inputSchema={"type": "object"},
        )
        mgr = _make_session_manager()
        tool = MCPTool(mcp_tool=mcp_tool, mcp_session_manager=mgr)
        assert tool.description == ""

    def test_filters_passed(self):
        mock_filter = MagicMock()
        mock_filter.name = "my_filter"
        tool, _ = _make_mcp_tool_instance(filters=[mock_filter])
        assert len(tool._filters) == 1


# ---------------------------------------------------------------------------
# Tests: _clean_schema
# ---------------------------------------------------------------------------

class TestCleanSchema:
    def _tool(self):
        tool, _ = _make_mcp_tool_instance()
        return tool

    def test_empty_schema(self):
        tool = self._tool()
        assert tool._clean_schema({}) == {}

    def test_none_schema(self):
        tool = self._tool()
        assert tool._clean_schema(None) is None

    def test_falsy_schema(self):
        tool = self._tool()
        assert tool._clean_schema(0) == 0

    def test_converts_dollar_defs_to_defs(self):
        tool = self._tool()
        schema = {
            "$defs": {
                "Foo": {"type": "string"},
            },
            "type": "object",
        }
        result = tool._clean_schema(schema)
        assert "defs" in result
        assert "$defs" not in result
        assert result["defs"]["Foo"] == {"type": "string"}

    def test_converts_dollar_ref_to_ref(self):
        tool = self._tool()
        schema = {"$ref": "#/$defs/Foo"}
        result = tool._clean_schema(schema)
        assert result["ref"] == "#/defs/Foo"
        assert "$ref" not in result

    def test_converts_anyOf_to_any_of(self):
        tool = self._tool()
        schema = {
            "anyOf": [
                {"type": "string"},
                {"type": "integer"},
            ]
        }
        result = tool._clean_schema(schema)
        assert "any_of" in result
        assert "anyOf" not in result
        assert len(result["any_of"]) == 2

    def test_removes_unsupported_fields(self):
        tool = self._tool()
        schema = {
            "type": "object",
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$id": "some-id",
            "definitions": {"Foo": {"type": "string"}},
            "const": "fixed_value",
        }
        result = tool._clean_schema(schema)
        assert "type" in result
        assert "$schema" not in result
        assert "$id" not in result
        assert "definitions" not in result
        assert "const" not in result

    def test_normalizes_type_list_to_first_element(self):
        tool = self._tool()
        schema = {"type": ["string", "null"]}
        result = tool._clean_schema(schema)
        assert result["type"] == "string"

    def test_normalizes_empty_type_list_unchanged(self):
        tool = self._tool()
        schema = {"type": []}
        result = tool._clean_schema(schema)
        assert result["type"] == []

    def test_recursively_cleans_properties(self):
        tool = self._tool()
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": ["string", "null"], "$ref": "#/$defs/X"},
            },
        }
        result = tool._clean_schema(schema)
        prop = result["properties"]["name"]
        assert prop["type"] == "string"
        assert prop.get("ref") == "#/defs/X"

    def test_recursively_cleans_items(self):
        tool = self._tool()
        schema = {
            "type": "array",
            "items": {"$ref": "#/$defs/Item"},
        }
        result = tool._clean_schema(schema)
        assert result["items"]["ref"] == "#/defs/Item"

    def test_recursively_cleans_any_of(self):
        tool = self._tool()
        schema = {
            "any_of": [
                {"$ref": "#/$defs/A"},
                {"type": "string"},
            ]
        }
        result = tool._clean_schema(schema)
        assert result["any_of"][0]["ref"] == "#/defs/A"

    def test_recursively_cleans_defs_entries(self):
        tool = self._tool()
        schema = {
            "$defs": {
                "Alert": {
                    "type": "object",
                    "properties": {
                        "msg": {"type": ["string", "null"]},
                    },
                },
            },
            "type": "object",
        }
        result = tool._clean_schema(schema)
        alert_props = result["defs"]["Alert"]["properties"]
        assert alert_props["msg"]["type"] == "string"

    def test_does_not_modify_original_schema(self):
        tool = self._tool()
        original = {
            "$defs": {"A": {"type": "string"}},
            "$ref": "#/$defs/A",
            "type": "object",
        }
        original_copy = original.copy()
        tool._clean_schema(original)
        assert "$defs" in original
        assert "$ref" in original

    def test_anyOf_with_non_dict_items(self):
        tool = self._tool()
        schema = {
            "anyOf": [
                {"type": "string"},
                "raw_value",
            ]
        }
        result = tool._clean_schema(schema)
        assert result["any_of"][1] == "raw_value"

    def test_complex_nested_schema(self):
        tool = self._tool()
        schema = {
            "type": "object",
            "$defs": {
                "DailyForecast": {
                    "type": "object",
                    "properties": {
                        "alerts": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/Alert"},
                        },
                    },
                },
                "Alert": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string"},
                    },
                },
            },
            "properties": {
                "forecast": {"$ref": "#/$defs/DailyForecast"},
            },
        }
        result = tool._clean_schema(schema)
        assert "defs" in result
        assert result["properties"]["forecast"]["ref"] == "#/defs/DailyForecast"
        items_ref = result["defs"]["DailyForecast"]["properties"]["alerts"]["items"]
        assert items_ref["ref"] == "#/defs/Alert"


# ---------------------------------------------------------------------------
# Tests: _get_declaration
# ---------------------------------------------------------------------------

class TestGetDeclaration:
    def test_returns_function_declaration(self):
        tool, _ = _make_mcp_tool_instance(
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        decl = tool._get_declaration()
        assert isinstance(decl, FunctionDeclaration)
        assert decl.name == "test_tool"
        assert decl.description == "A test tool"
        assert decl.parameters is not None

    def test_empty_input_schema(self):
        mcp_tool = McpBaseTool(name="t", description="d", inputSchema={})
        mgr = _make_session_manager()
        tool = MCPTool(mcp_tool=mcp_tool, mcp_session_manager=mgr)
        # Patch _mcp_tool.inputSchema to simulate None at declaration time
        tool._mcp_tool.inputSchema = None
        decl = tool._get_declaration()
        assert decl.parameters is None

    def test_with_output_schema(self):
        mcp_tool = McpBaseTool(
            name="t",
            description="d",
            inputSchema={"type": "object"},
            outputSchema={"type": "string"},
        )
        mgr = _make_session_manager()
        tool = MCPTool(mcp_tool=mcp_tool, mcp_session_manager=mgr)
        decl = tool._get_declaration()
        assert decl.response is not None


# ---------------------------------------------------------------------------
# Tests: _parse_mcp_call_tool_result_to_str
# ---------------------------------------------------------------------------

class TestParseMcpCallToolResult:
    def _tool(self):
        tool, _ = _make_mcp_tool_instance()
        return tool

    def test_error_result(self):
        tool = self._tool()
        result = CallToolResult(
            isError=True,
            content=[TextContent(type="text", text="something failed")],
        )
        assert tool._parse_mcp_call_tool_result_to_str(result) == "Error: something failed"

    def test_text_content(self):
        tool = self._tool()
        result = CallToolResult(
            isError=False,
            content=[TextContent(type="text", text="hello world")],
        )
        assert tool._parse_mcp_call_tool_result_to_str(result) == "hello world"

    def test_image_content(self):
        tool = self._tool()
        result = CallToolResult(
            isError=False,
            content=[ImageContent(type="image", data="base64data", mimeType="image/png")],
        )
        assert tool._parse_mcp_call_tool_result_to_str(result) == "base64data"

    def test_resource_content_with_text(self):
        tool = self._tool()
        resource = TextResourceContents(uri="file:///test.txt", text="resource text", mimeType="text/plain")
        result = CallToolResult(
            isError=False,
            content=[EmbeddedResource(type="resource", resource=resource)],
        )
        assert tool._parse_mcp_call_tool_result_to_str(result) == "resource text"

    def test_resource_content_with_blob(self):
        tool = self._tool()
        resource = BlobResourceContents(uri="file:///test.bin", blob="blob_data", mimeType="application/octet-stream")
        result = CallToolResult(
            isError=False,
            content=[EmbeddedResource(type="resource", resource=resource)],
        )
        assert tool._parse_mcp_call_tool_result_to_str(result) == "blob_data"

    def test_multiple_contents_returns_first_text(self):
        tool = self._tool()
        result = CallToolResult(
            isError=False,
            content=[
                TextContent(type="text", text="first"),
                TextContent(type="text", text="second"),
            ],
        )
        assert tool._parse_mcp_call_tool_result_to_str(result) == "first"

    def test_fallback_returns_raw_content(self):
        """When no content type matches, raw content list is returned."""
        tool = self._tool()
        result = MagicMock()
        result.isError = False
        mock_content = MagicMock()
        mock_content.type = "unknown"
        result.content = [mock_content]
        ret = tool._parse_mcp_call_tool_result_to_str(result)
        assert ret == result.content


# ---------------------------------------------------------------------------
# Tests: _run_async_impl
# ---------------------------------------------------------------------------

class TestRunAsyncImpl:
    @pytest.mark.asyncio
    async def test_calls_session_and_returns_result(self):
        tool, mgr = _make_mcp_tool_instance()
        mock_session = AsyncMock()
        mgr.create_session.return_value = mock_session
        mock_session.call_tool.return_value = CallToolResult(
            isError=False,
            content=[TextContent(type="text", text="ok")],
        )

        mock_ctx = MagicMock()
        result = await tool._run_async_impl(args={"key": "value"}, tool_context=mock_ctx)
        assert result == "ok"
        mock_session.call_tool.assert_awaited_once_with("test_tool", arguments={"key": "value"})

    @pytest.mark.asyncio
    async def test_raises_on_session_error(self):
        tool, mgr = _make_mcp_tool_instance()
        mgr.create_session.side_effect = RuntimeError("connection failed")

        mock_ctx = MagicMock()
        with pytest.raises(RuntimeError, match="connection failed"):
            await tool._run_async_impl(args={}, tool_context=mock_ctx)

    @pytest.mark.asyncio
    async def test_raises_on_call_tool_error(self):
        tool, mgr = _make_mcp_tool_instance()
        mock_session = AsyncMock()
        mgr.create_session.return_value = mock_session
        mock_session.call_tool.side_effect = Exception("tool execution failed")

        mock_ctx = MagicMock()
        with pytest.raises(Exception, match="tool execution failed"):
            await tool._run_async_impl(args={}, tool_context=mock_ctx)
