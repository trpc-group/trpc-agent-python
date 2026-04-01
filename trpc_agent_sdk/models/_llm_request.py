# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
#
# Directly reuse the types from adk-python
# Below code are copy and modified from https://github.com/google/adk-python.git
#
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""LLM request class for TRPC Agent framework."""

from __future__ import annotations

from typing import Any
from typing import Optional
from typing import Set
from typing_extensions import override

from pydantic import BaseModel

from trpc_agent_sdk.abc import RequestABC
from trpc_agent_sdk.types import GenerateContentConfig
from trpc_agent_sdk.types import Tool


class LlmRequest(RequestABC):
    """LLM request class for TRPC Agent framework.

    This class allows passing in tools, output schema and system
    instructions to the model.

    instructions to the model.

    Attributes:
      model: The model name.
      contents: The contents to send to the model.
      config: Additional config for the generate content request.
      tools_dict: The tools dictionary.
      streaming_tool_names: Names of tools that should receive streaming arguments.
    """

    streaming_tool_names: Optional[Set[str]] = None
    """Names of tools that should receive streaming arguments.

    When set, only tool calls for these tools will generate streaming events.
    When None or empty, no streaming events will be generated for tool calls.
    """

    @override
    def append_instructions(self, instructions: list[str]) -> None:
        """Appends instructions to the system instruction.

        Args:
          instructions: The instructions to append.
        """

        # Ensure config exists
        if self.config is None:
            self.config = GenerateContentConfig()

        new_instructions = "\n\n".join(instructions)

        if self.config.system_instruction:
            # For simplicity, we'll convert to string and append
            # Note: this assumes system_instruction is already a string or can be converted
            existing_str = str(self.config.system_instruction) if self.config.system_instruction else ""
            self.config.system_instruction = existing_str + "\n\n" + new_instructions
        else:
            self.config.system_instruction = new_instructions

    @override
    def append_tools(self, tools: list[Any]) -> None:
        """Appends tools to the request.

        Args:
          tools: The tools to append.
        """

        if not tools:
            return

        # Ensure config exists
        if self.config is None:
            self.config = GenerateContentConfig()

        # Ensure tools list exists
        if self.config.tools is None:
            self.config.tools = []

        declarations = []
        for tool in tools:
            declaration = tool._get_declaration()
            if declaration:
                declarations.append(declaration)
                self.tools_dict[tool.name] = tool
        if declarations:
            self.config.tools.append(Tool(function_declarations=declarations))

    @override
    def set_output_schema(self, base_model: type[BaseModel]) -> None:
        """Sets the output schema for the request.

        Args:
          base_model: The pydantic base model to set the output schema to.
        """

        # Ensure config exists
        if self.config is None:
            self.config = GenerateContentConfig()

        self.config.response_schema = base_model
        self.config.response_mime_type = "application/json"
