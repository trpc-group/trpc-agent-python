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
from typing import Optional

from trpc_agent_sdk.types import FunctionCall

from ._base import OpenAIAdapter
from ._base import ToolPromptTextFilterMixin


class HunyuanHy3PreviewAdapter(ToolPromptTextFilterMixin, OpenAIAdapter):
    """Provider-specific behavior for the hy3-preview model."""

    def __init__(self, model_name: str, base_url: Optional[str] = None):
        super().__init__(model_name=model_name, base_url=base_url)

    def parse_tool_prompt_function_calls(self, content: str, tool_prompt: Any) -> List[FunctionCall]:
        function_calls = self._parse_hunyuan_tool_calls(content)
        if function_calls:
            return function_calls
        return tool_prompt.parse_function(content)

    def requires_add_tools_to_prompt(self) -> bool:
        return True

    def should_filter_reasoning_text(self) -> bool:
        return True

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
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
