# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""DeepSeek adapter for OpenAI-compatible chat completions."""

from __future__ import annotations

from typing import Any
from typing import Optional

from trpc_agent_sdk.log import logger

from .. import _constants as const
from ._base import OpenAIAdapter
from ._base import has_reasoning_content


class DeepSeekAdapter(OpenAIAdapter):
    """Provider-specific behavior for DeepSeek's OpenAI-compatible API."""

    def __init__(self, model_name: str, base_url: Optional[str] = None):
        super().__init__(model_name=model_name, base_url=base_url)
        self._model_name_lower = model_name.lower()

    def is_v4_model(self) -> bool:
        """Return whether the current model uses DeepSeek v4 chat completions."""
        return self._model_name_lower.startswith("deepseek-v4-")

    def use_max_tokens_only(self) -> bool:
        return True

    def should_skip_config_param(self, param_name: str) -> bool:
        return param_name in {
            "frequency_penalty",
            "presence_penalty",
            "seed",
            "candidate_count",
        }

    def should_include_thought_signature(self) -> bool:
        return False

    def should_backfill_reasoning_content(self, role: str, message: dict[str, Any]) -> bool:
        if not self.is_v4_model() or role != const.ASSISTANT:
            return False
        if has_reasoning_content(message):
            return False
        return bool(message.get(const.CONTENT) or message.get(const.TOOL_CALLS))

    def build_response_format(self, config: Any) -> tuple[bool, Optional[dict[str, Any]]]:
        if config.response_mime_type != "application/json":
            return False, None
        if config.response_schema or config.response_json_schema:
            logger.warning("DeepSeek only supports JSON object response_format; response schema is ignored.")
        return True, {"type": "json_object"}

    def supports_response_schema(self) -> bool:
        """DeepSeek accepts JSON mode but ignores native response schemas."""
        return False

    def apply_thinking(self, request: Any, http_options: dict[str, Any]) -> bool:
        if not self.is_v4_model():
            return False
        if not request.config or not request.config.thinking_config:
            return False

        thinking_config = request.config.thinking_config
        if "extra_body" not in http_options:
            http_options["extra_body"] = {}
        processed_extra_body = http_options["extra_body"]
        thinking_body = dict(processed_extra_body.get("thinking") or {})

        if thinking_config.include_thoughts and thinking_config.thinking_budget != 0:
            thinking_body["type"] = "enabled"
            thinking_body.setdefault(
                "reasoning_effort",
                "max" if thinking_config.thinking_budget and thinking_config.thinking_budget > 0 else "high",
            )
        else:
            thinking_body["type"] = "disabled"

        processed_extra_body["thinking"] = thinking_body
        return True
