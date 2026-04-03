# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""XML tool prompt class for TRPC Agent framework."""

import json
import re
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Tool

from ._base import ToolPrompt


class XmlToolPrompt(ToolPrompt):
    """XML tool prompt implementation based on Anthropic's format."""

    def __init__(self):
        """Initialize the XML tool prompt."""
        super().__init__()

    @override
    def build_prompt(self, tools: List[Tool]) -> str:
        """Build XML tool prompt from tools.

        Args:
            tools: List of Tool objects to convert to XML prompt

        Returns:
            XML-formatted tool prompt string
        """
        if not tools:
            return ""

        tool_descriptions = []

        for tool in tools:
            if tool.function_declarations:
                for func_decl in tool.function_declarations:
                    tool_desc = self._format_tool_description(
                        func_decl.name or "",
                        func_decl.description or "",
                        func_decl.parameters,
                    )
                    tool_descriptions.append(tool_desc)

        if not tool_descriptions:
            return ""

        tool_use_prompt = (
            "In this environment you have access to a set of tools you can use to answer the user's question.\n"
            "\n"
            "Here are the tools available:\n"
            "<tools>\n" + "\n".join(tool_descriptions) + "\n</tools>\n"
            "\n"
            "You MUST call them by using below format:\n"
            "<function_calls>\n"
            "<invoke>\n"
            "<tool_name>$TOOL_NAME</tool_name>\n"
            "<parameters>\n"
            "<$PARAMETER_NAME>$PARAMETER_VALUE</$PARAMETER_NAME>\n"
            "...\n"
            "</parameters>\n"
            "</invoke>\n"
            "</function_calls>\n"
            "\n"
            "For example, you can call search tool with below text:\n"
            "<function_calls>\n"
            "<invoke>\n"
            "<tool_name>search</tool_name>\n"
            "<parameters>\n"
            "<query>Where can i buy a house</query>\n"
            "</parameters>\n"
            "</invoke>\n"
            "</function_calls>\n")

        return tool_use_prompt

    def _format_tool_description(self, name: str, description: str, parameters) -> str:
        """Format a single tool description in XML format.

        Args:
            name: Tool name
            description: Tool description
            parameters: Tool parameters schema

        Returns:
            XML-formatted tool description
        """
        params_str = self._format_parameters(parameters)

        return ("<tool_description>\n"
                f"<tool_name>{name}</tool_name>\n"
                "<description>\n"
                f"{description}\n"
                "</description>\n"
                "<parameters>\n"
                f"{params_str}\n"
                "</parameters>\n"
                "</tool_description>")

    def _format_parameters(self, parameters) -> str:
        """Format parameters schema for XML display.

        Args:
            parameters: Parameters schema object

        Returns:
            Formatted parameters string
        """
        if not parameters:
            return ""

        try:
            # Convert parameters to a readable format
            if hasattr(parameters, "properties") and parameters.properties:
                param_lines = []
                for param_name, param_schema in parameters.properties.items():
                    param_type = getattr(param_schema, "type", "string")
                    if hasattr(param_type, "value"):
                        param_type = param_type.value
                    param_desc = getattr(param_schema, "description", "")

                    param_line = f"<{param_name}> ({param_type})"
                    if param_desc:
                        param_line += f": {param_desc}"
                    param_line += f" </{param_name}>"
                    param_lines.append(param_line)

                return "\n".join(param_lines)
            else:
                # Fallback to JSON representation
                return str(parameters)
        except Exception:  # pylint: disable=broad-except
            return str(parameters)

    @override
    def parse_function(self, content: str) -> List[FunctionCall]:
        """Parse function calls from complete XML content.

        Args:
            content: Complete content string containing XML function calls

        Returns:
            List of FunctionCall objects parsed from content

        Raises:
            ValueError: If content cannot be parsed as XML function calls
        """
        function_calls = []

        # Find all function_calls blocks
        function_calls_pattern = r"<function_calls>(.*?)</function_calls>"
        matches = re.findall(function_calls_pattern, content, re.DOTALL)

        for match in matches:
            # Parse each invoke block within function_calls
            invoke_pattern = r"<invoke>(.*?)</invoke>"
            invoke_matches = re.findall(invoke_pattern, match, re.DOTALL)

            for invoke_content in invoke_matches:
                try:
                    func_call = self._parse_single_invoke(invoke_content)
                    if func_call:
                        function_calls.append(func_call)
                except Exception as ex:  # pylint: disable=broad-except
                    raise ValueError(f"Failed to parse function call: {ex}")

        return function_calls

    def _parse_single_invoke(self, invoke_content: str) -> Optional[FunctionCall]:
        """Parse a single invoke block.

        Args:
            invoke_content: Content of an invoke block

        Returns:
            FunctionCall object or None if parsing fails
        """
        try:
            # Extract tool name
            tool_name_match = re.search(r"<tool_name>(.*?)</tool_name>", invoke_content, re.DOTALL)
            if not tool_name_match:
                return None

            tool_name = tool_name_match.group(1).strip()

            # Extract parameters
            parameters_match = re.search(r"<parameters>(.*?)</parameters>", invoke_content, re.DOTALL)
            parameters = {}

            if parameters_match:
                params_content = parameters_match.group(1).strip()
                # Parse individual parameter tags
                param_pattern = r"<(\w+)>(.*?)</\1>"
                param_matches = re.findall(param_pattern, params_content, re.DOTALL)

                for param_name, param_value in param_matches:
                    # Try to parse as JSON if it looks like JSON, otherwise keep as string
                    param_value = param_value.strip()
                    try:
                        if param_value.startswith(("{", "[", '"')) or param_value in ("true", "false", "null"):
                            parameters[param_name] = json.loads(param_value)
                        else:
                            # Try to convert numbers
                            if param_value.isdigit():
                                parameters[param_name] = int(param_value)
                            elif param_value.replace(".", "").isdigit():
                                parameters[param_name] = float(param_value)
                            else:
                                parameters[param_name] = param_value
                    except json.JSONDecodeError:
                        parameters[param_name] = param_value

            return FunctionCall(name=tool_name, args=parameters)

        except Exception:  # pylint: disable=broad-except
            return None
