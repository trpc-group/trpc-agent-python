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
"""LLM response class for TRPC Agent framework."""

from typing_extensions import override

from trpc_agent_sdk.abc import ResponseABC
from trpc_agent_sdk.types import GenerateContentResponse


class LlmResponse(ResponseABC):
    """LLM response class for TRPC Agent framework.

    This class provides the first candidate response from the

    If available, otherwise returns error code and message.

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

    def has_content(self) -> bool:
        """Returns whether the response carries user-visible content (text or function call).

        Returns True if any content part contains text or a function call. Parts that
        only hold function responses, executable code, or code execution results are
        not considered content for this check.
        """
        if not self.content or not self.content.parts:
            return False
        return any(p.text or p.function_call for p in self.content.parts)

    @override
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
        usage_metadata = generate_content_response.usage_metadata
        if generate_content_response.candidates:
            candidate = generate_content_response.candidates[0]
            if candidate.content and candidate.content.parts:
                return LlmResponse(
                    content=candidate.content,
                    grounding_metadata=candidate.grounding_metadata,
                    usage_metadata=usage_metadata,
                )
            else:
                return LlmResponse(
                    error_code=candidate.finish_reason,
                    error_message=candidate.finish_message,
                    usage_metadata=usage_metadata,
                )
        else:
            if generate_content_response.prompt_feedback:
                prompt_feedback = generate_content_response.prompt_feedback
                return LlmResponse(
                    error_code=prompt_feedback.block_reason,
                    error_message=prompt_feedback.block_reason_message,
                    usage_metadata=usage_metadata,
                )
            else:
                return LlmResponse(
                    error_code="UNKNOWN_ERROR",
                    error_message="Unknown error.",
                    usage_metadata=usage_metadata,
                )
