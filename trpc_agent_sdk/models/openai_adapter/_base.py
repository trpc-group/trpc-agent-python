# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Base adapter for OpenAI-compatible model provider differences."""

from __future__ import annotations

from typing import Any
from typing import List
from typing import Optional

from trpc_agent_sdk.types import FunctionCall

from .. import _constants as const

_TOOL_PROMPT_MARKERS = (
    "<tools",
    "<tool_description",
    "<tool_name",
    "<parameters",
    "<tool_call",
    "<function_calls",
    "<invoke",
    "<tool_sep",
)
_TOOL_PROMPT_MARKER_ENDS = {
    "<tools": "</tools>",
    "<tool_description": "</tool_description>",
    "<tool_name": "</tool_name>",
    "<parameters": "</parameters>",
    "<tool_call": "</tool_call>",
    "<function_calls": "</function_calls>",
    "<invoke": "</invoke>",
    "<tool_sep": "</tool_call>",
}
_TOOL_PROMPT_MARKER_LOOKBEHIND = max(
    max(len(marker) for marker in _TOOL_PROMPT_MARKERS),
    max(len(marker) for marker in _TOOL_PROMPT_MARKER_ENDS.values()),
) - 1


class OpenAIAdapter:
    """Adapter hook points for provider-specific OpenAI-compatible behavior."""

    def __init__(self, model_name: str, base_url: Optional[str] = None):
        self.model_name = model_name
        self.base_url = base_url

    def use_max_tokens_only(self) -> bool:
        """Whether max_output_tokens should map only to max_tokens."""
        return False

    def should_skip_config_param(self, param_name: str) -> bool:
        """Whether a GenerateContentConfig field should be skipped for this provider."""
        return False

    def should_include_thought_signature(self) -> bool:
        """Whether tool call history should include thought_signature."""
        return True

    def should_backfill_reasoning_content(self, role: str, message: dict[str, Any]) -> bool:
        """Whether assistant history should include an empty reasoning_content field."""
        return False

    def build_response_format(self, config: Any) -> tuple[bool, Optional[dict[str, Any]]]:
        """Return provider-specific response_format.

        The first tuple item indicates whether the adapter handled the config.
        """
        return False, None

    def supports_response_schema(self) -> bool:
        """Whether the provider supports native JSON schema response formats."""
        return True

    def apply_thinking(self, request: Any, http_options: dict[str, Any]) -> bool:
        """Apply provider-specific thinking options.

        Returns True when the adapter handled thinking and the default OpenAI
        thinking mapping should be skipped.
        """
        return False

    def parse_tool_prompt_function_calls(self, content: str, tool_prompt: Any) -> List[FunctionCall]:
        """Parse text-form tool calls emitted by a provider."""
        return tool_prompt.parse_function(content)

    def requires_add_tools_to_prompt(self) -> bool:
        """Whether this adapter requires ToolPrompt mode when tools are used."""
        return False

    def should_suppress_tool_prompt_text(self) -> bool:
        """Whether parsed text-form tool calls should be hidden from final text."""
        return False

    def should_filter_reasoning_text(self) -> bool:
        """Whether ToolPrompt filtering should also apply to reasoning_content."""
        return False

    def create_streaming_text_filter_state(self) -> dict[str, Any]:
        """Create per-stream state for filtering provider-specific text chunks."""
        return {}

    def filter_streaming_text(self, text: str, state: dict[str, Any]) -> str:
        """Filter a streaming text chunk before yielding it to users."""
        return text

    def flush_streaming_text(self, state: dict[str, Any]) -> str:
        """Flush any buffered streaming text after the stream ends."""
        return ""


class DefaultOpenAIAdapter(OpenAIAdapter):
    """Default OpenAI-compatible adapter with no provider overrides."""

    pass


class ToolPromptTextFilterMixin:
    """Opt-in filtering for models that emit ToolPrompt XML as streamed text."""

    def should_suppress_tool_prompt_text(self) -> bool:
        return True

    def create_streaming_text_filter_state(self) -> dict[str, Any]:
        return {
            "buffer": "",
            "suppress": False,
            "suppress_until": "",
        }

    def filter_streaming_text(self, text: str, state: dict[str, Any]) -> str:
        if state.get("suppress"):
            buffer = f"{state.get('buffer', '')}{text}"
            suppress_until = state.get("suppress_until") or ""
            marker_start = buffer.find(suppress_until) if suppress_until else -1
            if marker_start < 0:
                state["buffer"] = buffer[-_TOOL_PROMPT_MARKER_LOOKBEHIND:]
                return ""

            resume_at = marker_start + len(suppress_until)
            state["buffer"] = ""
            state["suppress"] = False
            state["suppress_until"] = ""
            return self.filter_streaming_text(buffer[resume_at:], state)

        buffer = f"{state.get('buffer', '')}{text}"
        marker_start, marker = self._find_first_tool_prompt_marker(buffer)
        if marker:
            state["buffer"] = ""
            state["suppress"] = True
            state["suppress_until"] = _TOOL_PROMPT_MARKER_ENDS[marker]
            return buffer[:marker_start] + self.filter_streaming_text(buffer[marker_start:], state)

        if len(buffer) <= _TOOL_PROMPT_MARKER_LOOKBEHIND:
            state["buffer"] = buffer
            return ""

        split_at = len(buffer) - _TOOL_PROMPT_MARKER_LOOKBEHIND
        state["buffer"] = buffer[split_at:]
        return buffer[:split_at]

    def flush_streaming_text(self, state: dict[str, Any]) -> str:
        if state.get("suppress"):
            return ""

        buffer = state.get("buffer", "")
        state["buffer"] = ""
        marker_start, marker = self._find_first_tool_prompt_marker(buffer)
        if marker:
            state["suppress"] = True
            state["suppress_until"] = _TOOL_PROMPT_MARKER_ENDS[marker]
            return buffer[:marker_start]
        return buffer

    def _find_first_tool_prompt_marker(self, text: str) -> tuple[int, Optional[str]]:
        marker_positions = [(text.find(marker), marker) for marker in _TOOL_PROMPT_MARKERS if marker in text]
        if not marker_positions:
            return -1, None
        return min(marker_positions, key=lambda item: item[0])


def has_reasoning_content(message: dict[str, Any]) -> bool:
    """Return whether message already includes reasoning_content."""
    return const.REASONING_CONTENT in message
