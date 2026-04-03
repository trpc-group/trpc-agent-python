# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""MCP Tool implementation for TRPC Agent framework.

This module provides integration between TRPC Agent tools and MCP (Microservice Control Platform)
services, enabling agents to utilize MCP tools as part of their toolset.
"""

from __future__ import annotations

from typing import Optional
from typing_extensions import override

from mcp.types import CallToolResult
from mcp.types import Tool as McpBaseTool

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.filter import BaseFilter
from trpc_agent_sdk.log import logger
from trpc_agent_sdk.types import FunctionDeclaration
from trpc_agent_sdk.types import Schema

from .._base_tool import BaseTool
from ..utils import SCHEMA_FIELDS
from ..utils import retry_on_closed_resource
from ._mcp_session_manager import MCPSessionManager


class MCPTool(BaseTool):
    """Turns an MCP Tool into a Tool for trpc_agent_sdk.

     Internally, the tool initializes from a MCP Tool, and uses the MCP Session to
     call the tool.

     Note: For API key authentication, only header-based API keys are supported.
     Query and cookie-based API keys will result in authentication errors.trpc_agent_sdk.
     """

    def __init__(
        self,
        *,
        mcp_tool: McpBaseTool,
        mcp_session_manager: MCPSessionManager,
        filters_name: Optional[list[str]] = None,
        filters: Optional[list[BaseFilter]] = None,
    ):
        """Initializes an MCPTool.

        This tool wraps an MCP Tool interface and uses a session manager to
        communicate with the MCP server.

        Args:
            mcp_tool: The MCP tool to wrap.
            mcp_session_manager: The MCP session manager to use for communication.
            filters_name: List of filter names
            filters: List of filter instances

        Raises:
            ValueError: If mcp_tool or mcp_session_manager is None.
        """
        if not mcp_tool or not mcp_session_manager:
            raise ValueError(f"some param cannot be None mcp_tool: {mcp_tool}, "
                             f"mcp_session_manager: {mcp_session_manager}")
        super().__init__(name=mcp_tool.name,
                         description=mcp_tool.description or "",
                         filters_name=filters_name,
                         filters=filters)
        self._mcp_tool = mcp_tool
        self._mcp_session_manager = mcp_session_manager

    def _clean_schema(self, schema: dict) -> dict:
        """Clean schema by removing unsupported fields and converting JSON Schema to GenAI Schema.

        This method handles the conversion from JSON Schema format (with $defs, $ref)
        to Google GenAI Schema format (with defs, ref).

        Args:
            schema: Raw schema dictionary from MCP tool

        Returns:
            dict: Cleaned schema compatible with Google GenAI Schema
        """
        if not schema:
            return schema

        # Create a copy to avoid modifying the original
        schema_dict = schema.copy()

        # Convert $defs to defs (JSON Schema -> GenAI Schema)
        if '$defs' in schema_dict:
            schema_dict['defs'] = schema_dict.pop('$defs')
            # Recursively clean each definition
            if isinstance(schema_dict['defs'], dict):
                for def_name, def_schema in schema_dict['defs'].items():
                    if isinstance(def_schema, dict):
                        schema_dict['defs'][def_name] = self._clean_schema(def_schema)

        # Convert $ref to ref and update the reference path
        if '$ref' in schema_dict:
            ref_value = schema_dict.pop('$ref')
            # Convert #/$defs/Foo to #/defs/Foo
            if isinstance(ref_value, str):
                schema_dict['ref'] = ref_value.replace('$defs', 'defs')

        # Convert anyOf to any_of and recursively clean each schema
        if 'anyOf' in schema_dict:
            any_of_schemas = schema_dict.pop('anyOf')
            if isinstance(any_of_schemas, list):
                schema_dict['any_of'] = [
                    self._clean_schema(sub_schema) if isinstance(sub_schema, dict) else sub_schema
                    for sub_schema in any_of_schemas
                ]

        # Remove fields that are not supported by Google GenAI Schema
        for field in list(schema_dict.keys()):
            if field not in SCHEMA_FIELDS:
                schema_dict.pop(field)

        # Normalize type field: if it's a list, take the first element
        if 'type' in schema_dict and isinstance(schema_dict['type'], list):
            if schema_dict['type']:  # Ensure the list is not empty
                schema_dict['type'] = schema_dict['type'][0]

        # Recursively clean nested properties
        if 'properties' in schema_dict and isinstance(schema_dict['properties'], dict):
            for prop_name, prop_schema in schema_dict['properties'].items():
                if isinstance(prop_schema, dict):
                    schema_dict['properties'][prop_name] = self._clean_schema(prop_schema)

        # Recursively clean array items, maily replace the mcp reference(#/$defs/Alert)
        #  to google genai format(#/defs/DailyForecast)
        if 'items' in schema_dict and isinstance(schema_dict['items'], dict):
            schema_dict['items'] = self._clean_schema(schema_dict['items'])

        # Recursively clean anyOf schemas
        if 'any_of' in schema_dict and isinstance(schema_dict['any_of'], list):
            schema_dict['any_of'] = [
                self._clean_schema(sub_schema) if isinstance(sub_schema, dict) else sub_schema
                for sub_schema in schema_dict['any_of']
            ]

        return schema_dict

    @override
    def _get_declaration(self) -> FunctionDeclaration:
        """Gets the function declaration for the tool.

        Returns:
            FunctionDeclaration: The Gemini function declaration for the tool.
        """
        input_schema = self._clean_schema(self._mcp_tool.inputSchema) if self._mcp_tool.inputSchema else None
        output_schema = self._clean_schema(self._mcp_tool.outputSchema) if self._mcp_tool.outputSchema else None

        function_decl = FunctionDeclaration(
            name=self.name,
            description=self.description,
            parameters=Schema.model_validate(input_schema) if input_schema else None,
            response=Schema.model_validate(output_schema) if output_schema else None,
        )
        return function_decl

    def _parse_mcp_call_tool_result_to_str(self, result: CallToolResult) -> str:
        """Converts MCP call result into standardized string format.

        Args:
            result: Raw result from MCP tool call

        Returns:
            str: Parsed result in string format
        """
        if result.isError:
            return f"Error: {result.content[0].text}"  # type: ignore
        for data in result.content:
            if data.type == "text":
                return data.text
            if data.type == "image":
                return data.data
            if data.type == "resource":
                text = getattr(data.resource, 'text', '')
                if not text:
                    text = getattr(data.resource, 'blob', '')
                return text
        return result.content  # type: ignore

    @retry_on_closed_resource
    @override
    async def _run_async_impl(self, *, args, tool_context: InvocationContext):
        """Runs the tool asynchronously.

        Args:
            args: The arguments as a dict to pass to the tool.
            tool_context: The tool context of the current invocation.

        Returns:
            Any: The response from the tool.
        """
        # Get the session from the session manager
        try:
            session = await self._mcp_session_manager.create_session()
            response = await session.call_tool(self.name, arguments=args)
            return self._parse_mcp_call_tool_result_to_str(response)
        except Exception as ex:  # pylint: disable=broad-except
            logger.error("Failed to call tool %s with args %s: %s", self.name, args, ex, exc_info=True)
            raise
