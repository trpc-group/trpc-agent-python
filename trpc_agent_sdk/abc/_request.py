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
"""Base request class for TRPC Agent framework."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any
from typing import Optional

from google.genai.types import Content
from google.genai.types import GenerateContentConfig
from google.genai.types import LiveConnectConfig
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class RequestABC(BaseModel):
    """LLM request class that allows passing in tools, output schema and system

    instructions to the model.

    Attributes:
      model: The model name.
      contents: The contents to send to the model.
      config: Additional config for the generate content request.
      tools_dict: The tools dictionary.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)
    """The pydantic model config."""

    model: Optional[str] = None
    """The model name."""

    contents: list[Content] = Field(default_factory=list)
    """The contents to send to the model."""

    config: Optional[GenerateContentConfig] = None
    """Additional config for the generate content request."""

    live_connect_config: LiveConnectConfig = LiveConnectConfig()
    """Additional config for the generate content request.

    tools in generate_content_config should not be set.
    """
    tools_dict: dict[str, Any] = Field(default_factory=dict, exclude=True)
    """The tools dictionary."""

    @abstractmethod
    def append_instructions(self, instructions: list[str]) -> None:
        """Appends instructions to the system instruction.

        Args:
          instructions: The instructions to append.
        """

    @abstractmethod
    def append_tools(self, tools: list[Any]) -> None:
        """Appends tools to the request.

        Args:
          tools: The tools to append.
        """

    @abstractmethod
    def set_output_schema(self, base_model: type[BaseModel]) -> None:
        """Sets the output schema for the request.

        Args:
          base_model: The pydantic base model to set the output schema to.
        """
