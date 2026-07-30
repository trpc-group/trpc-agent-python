# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Hunyuan adapter for OpenAI-compatible chat completions."""

from __future__ import annotations

import json
import re
from typing import Any
from typing import List

from trpc_agent_sdk.types import FunctionCall

from ._base import OpenAIAdapter
from ._base import ToolPromptTextFilterMixin


class HunyuanHy3PreviewAdapter(ToolPromptTextFilterMixin, OpenAIAdapter):
    """Provider-specific behavior for the hy3-preview model.

    hy3-preview does not support native OpenAI function calling: tools are
    injected into the prompt and tool calls are returned as
    ``<tool_call>NAME<tool_sep>...</tool_call>`` XML which we parse back here.
    """

    def requires_add_tools_to_prompt(self) -> bool:
        return True

    def should_filter_reasoning_text(self) -> bool:
        return True

    def parse_tool_prompt_function_calls(self, content: str, tool_prompt: Any) -> List[FunctionCall]:
        function_calls = self._parse_hunyuan_tool_calls(content)
        if function_calls:
            return function_calls
        return tool_prompt.parse_function(content)

    def _parse_hunyuan_tool_calls(self, content: str) -> List[FunctionCall]:
        function_calls = []
        matches = re.findall(r"<tool_call>(.*?)</tool_call>", content, re.DOTALL)

        for match in matches:
            if "<tool_sep>" not in match:
                continue

            tool_name, params_content = match.split("<tool_sep>", 1)
            args = self._parse_hunyuan_tool_args(params_content)
            function_calls.append(FunctionCall(name=tool_name.strip(), args=args))

        return function_calls

    def _parse_hunyuan_tool_args(self, params_content: str) -> dict[str, Any]:
        args: dict[str, Any] = {}
        param_matches = re.findall(
            r"<arg_key>(.*?)</arg_key>\s*<arg_value>(.*?)</arg_value>",
            params_content,
            re.DOTALL,
        )
        if param_matches:
            for key, value in param_matches:
                args[key.strip()] = self._parse_arg_value(value.strip())
            return args

        params_content = params_content.strip()
        if not params_content:
            return args

        parsed_value = self._parse_arg_value(params_content)
        if isinstance(parsed_value, dict):
            return parsed_value
        return {"value": parsed_value}

    def _parse_arg_value(self, value: str) -> Any:
        # Keep STRICT json.loads here. Hunyuan's <arg_value> tags often contain
        # plain text (e.g. "Beijing", "2025-01-01"). json_repair would silently
        # coerce such plain text into "" and skip the fallback branch below,
        # corrupting tool arguments. Real JSON-shaped values still parse, and
        # the fallback preserves the original string for everything else.
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value


class HunyuanHy3Adapter(OpenAIAdapter):
    """Provider-specific behavior for the Hunyuan hy3 model.

    Unlike hy3-preview, hy3 supports native OpenAI function calling, so it does
    not use the prompt-injected XML tool-call format and must not require
    ``add_tools_to_prompt``. It only needs the Hunyuan reasoning quirks: the
    model's previous reasoning_content must be replayed in the request instead
    of being stripped, and ``thought_signature`` (a Gemini-only concept) is
    disabled.
    """

    def should_preserve_reasoning_content(self) -> bool:
        return True

    def should_include_thought_signature(self) -> bool:
        return False
