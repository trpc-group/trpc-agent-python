# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
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
"""Base response class for TRPC Agent framework."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any
from typing import Optional

from google.genai.types import Content
from google.genai.types import GenerateContentResponse
from google.genai.types import GenerateContentResponseUsageMetadata
from google.genai.types import GroundingMetadata
from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import alias_generators


class ResponseABC(BaseModel):
    """LLM response class that provides the first candidate response from the

    model if available. Otherwise, returns error code and message.

    Attributes:
      content: The content of the response.
      grounding_metadata: The grounding metadata of the response.
      partial: Indicates whether the text content is part of a unfinished text
        stream. Only used for streaming mode and when the content is plain text.
      turn_complete: Indicates whether the response from the model is complete.
        Only used for streaming mode.
      error_code: Error code if the response is an error. Code varies by model.
      error_message: Error message if the response is an error.
      interrupted: Flag indicating that LLM was interrupted when generating the
        content. Usually it's due to user interruption during a bidi streaming.
      custom_metadata: The custom metadata of the LlmResponse.
    """

    model_config = ConfigDict(
        extra="forbid",
        alias_generator=alias_generators.to_camel,
        populate_by_name=True,
    )
    """The pydantic model config."""

    content: Optional[Content] = None
    """The content of the response."""

    grounding_metadata: Optional[GroundingMetadata] = None
    """The grounding metadata of the response."""

    partial: Optional[bool] = None
    """Indicates whether the text content is part of a unfinished text stream.

    Only used for streaming mode and when the content is plain text.
    """

    turn_complete: Optional[bool] = None
    """Indicates whether the response from the model is complete.

    Only used for streaming mode.
    """

    error_code: Optional[str] = None
    """Error code if the response is an error. Code varies by model."""

    error_message: Optional[str] = None
    """Error message if the response is an error."""

    interrupted: Optional[bool] = None
    """Flag indicating that LLM was interrupted when generating the content.
    Usually it's due to user interruption during a bidi streaming.
    """

    custom_metadata: Optional[dict[str, Any]] = None
    """The custom metadata of the LlmResponse.

    An optional key-value pair to label an LlmResponse.

    NOTE: the entire dict must be JSON serializable.
    """

    usage_metadata: Optional[GenerateContentResponseUsageMetadata] = None
    """The usage metadata of the LlmResponse"""

    response_id: Optional[str] = None
    """The response ID from the model API."""

    @abstractmethod
    def create(
        self,
        generate_content_response: GenerateContentResponse,
    ) -> ResponseABC:
        """Creates an LlmResponse from a GenerateContentResponse.

        Args:
          generate_content_response: The GenerateContentResponse to create the
            LlmResponse from.

        Returns:
          The LlmResponse.
        """
