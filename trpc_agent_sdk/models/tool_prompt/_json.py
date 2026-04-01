# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""JSON tool prompt class for TRPC Agent framework."""

import json
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing_extensions import override

from trpc_agent_sdk.types import FunctionCall
from trpc_agent_sdk.types import Tool

from ._base import ToolPrompt


class JsonToolPrompt(ToolPrompt):
    """JSON tool prompt implementation based on function calling format."""

    def __init__(self):
        """Initialize the JSON tool prompt."""
        super().__init__()

    @override
    def build_prompt(self, tools: List[Tool]) -> str:
        """Build JSON tool prompt from tools.

        Args:
            tools: List of Tool objects to convert to JSON prompt

        Returns:
            JSON-formatted tool prompt string
        """
        if not tools:
            return ""

        function_descriptions = []

        for tool in tools:
            if tool.function_declarations:
                for func_decl in tool.function_declarations:
                    func_desc = self._format_function_description(func_decl)
                    function_descriptions.append(func_desc)

        if not function_descriptions:
            return ""

        functions_json = json.dumps(function_descriptions, indent=2, ensure_ascii=False)

        prompt = ("Produce JSON OUTPUT ONLY! Adhere to this format"
                  '{"name": "function_name", "arguments":{"argument_name": "argument_value"}}\n'
                  "When you NOT call function, please DO NOT generate json code block.\n"
                  f"The following functions are available to you:\n{functions_json}")

        return prompt

    def _format_function_description(self, func_decl) -> Dict[str, Any]:
        """Format a function declaration as JSON description.

        Args:
            func_decl: Function declaration object

        Returns:
            Dictionary representing the function description
        """
        description = {
            "name": func_decl.name or "",
            "description": func_decl.description or "",
        }

        if func_decl.parameters:
            description["parameters"] = self._convert_schema_to_json(func_decl.parameters)
        else:
            description["parameters"] = {"type": "object", "properties": {}}

        return description

    def _convert_schema_to_json(self, schema) -> Dict[str, Any]:
        """Convert schema to JSON format.

        Args:
            schema: Schema object to convert

        Returns:
            Dictionary representing the schema in JSON format
        """
        if not schema:
            return {"type": "object", "properties": {}}

        result = {}

        # Handle type
        if hasattr(schema, "type") and schema.type:
            if hasattr(schema.type, "value"):
                result["type"] = schema.type.value.lower()
            else:
                result["type"] = str(schema.type).lower()
        else:
            result["type"] = "object"

        # Handle properties
        if hasattr(schema, "properties") and schema.properties:
            result["properties"] = {}
            for prop_name, prop_schema in schema.properties.items():
                result["properties"][prop_name] = self._convert_schema_to_json(prop_schema)
        else:
            if result.get("type") == "object":
                result["properties"] = {}

        # Handle description
        if hasattr(schema, "description") and schema.description:
            result["description"] = schema.description

        # Handle required fields
        if hasattr(schema, "required") and schema.required:
            result["required"] = schema.required

        # Handle items for arrays
        if hasattr(schema, "items") and schema.items:
            result["items"] = self._convert_schema_to_json(schema.items)

        # Handle additional properties
        if hasattr(schema, "additional_properties") and schema.additional_properties is not None:
            result["additionalProperties"] = schema.additional_properties

        return result

    @override
    def parse_function(self, content: str) -> List[FunctionCall]:
        """Parse function calls from complete JSON content.

        Args:
            content: Complete content string containing JSON function calls

        Returns:
            List of FunctionCall objects parsed from content

        Raises:
            ValueError: If content cannot be parsed as JSON function calls
        """
        function_calls = []

        # Try to find JSON objects in the content
        # Look for patterns like {"name": "...", "arguments": {...}}
        json_pattern = r'\{[^{}]*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:\s*\{[^{}]*\}[^{}]*\}'

        # Also look for nested JSON objects
        brace_count = 0
        start_pos = -1

        for i, char in enumerate(content):
            if char == "{":
                if brace_count == 0:
                    start_pos = i
                brace_count += 1
            elif char == "}":
                brace_count -= 1
                if brace_count == 0 and start_pos != -1:
                    # Found a complete JSON object
                    json_str = content[start_pos:i + 1]
                    try:
                        func_call = self._parse_json_function_call(json_str)
                        if func_call:
                            function_calls.append(func_call)
                    except Exception:  # pylint: disable=broad-except
                        # Try regex pattern as fallback
                        pass

        # Fallback to regex pattern
        if not function_calls:
            matches = re.findall(json_pattern, content, re.DOTALL)
            for match in matches:
                try:
                    func_call = self._parse_json_function_call(match)
                    if func_call:
                        function_calls.append(func_call)
                except Exception as ex:  # pylint: disable=broad-except
                    raise ValueError(f"Failed to parse JSON function call: {ex}")

        return function_calls

    def _parse_json_function_call(self, json_str: str) -> Optional[FunctionCall]:
        """Parse a single JSON function call.

        Args:
            json_str: JSON string representing a function call

        Returns:
            FunctionCall object or None if parsing fails
        """
        try:
            data = json.loads(json_str.strip())

            if not isinstance(data, dict):
                return None

            name = data.get("name")
            if not name:
                return None

            arguments = data.get("arguments", {})
            if not isinstance(arguments, dict):
                # Try to parse arguments as JSON string
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                else:
                    arguments = {}

            return FunctionCall(name=name, args=arguments)

        except json.JSONDecodeError:
            return None
        except Exception:  # pylint: disable=broad-except
            return None
