# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Secret redaction filter for the code review agent.

Intercepts and masks sensitive information (API keys, passwords, tokens)
before they reach the LLM, sandbox, or get stored in the database.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from trpc_agent_sdk.filter import BaseFilter, FilterResult, register_tool_filter


@register_tool_filter("secret_redaction_filter")
class SecretRedactionFilter(BaseFilter):
    """敏感信息脱敏 Filter：检测并替换 API Key/Token/Password 等敏感信息。

    在 LLM 输入前和输出后执行脱敏，确保模型不会看到明文敏感信息，
    同时报告和数据库记录中也不出现明文。
    """

    # 12 种敏感信息模式
    SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
        (re.compile(r'(?i)(?:api_key|api[_-]?key|apikey)\s*[=:]\s*[\'"](sk-[a-zA-Z0-9]{10,})[\'"]'),
         "API Key"),
        (re.compile(r'(?i)(?:password|passwd|pwd)\s*[=:]\s*[\'"][^\'"]{4,}[\'"]'),
         "Password"),
        (re.compile(r"ghp_[a-zA-Z0-9]{36,}"),
         "GitHub Token"),
        (re.compile(r"AKIA[0-9A-Z]{16}"),
         "AWS Access Key"),
        (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
         "Private Key"),
        (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
         "JWT Token"),
        (re.compile(r"(?:postgres(?:ql)?|mysql|redis)://[^:]+:[^@]+@"),
         "DB Connection String"),
        (re.compile(r'(?i)(?:token|secret|credential)\s*[=:]\s*[\'"][^\'"]{8,}[\'"]'),
         "Generic Secret"),
        (re.compile(r"(?i)sk-[a-zA-Z0-9]{20,}"),
         "OpenAI API Key"),
        (re.compile(r"(?i)pk-[a-zA-Z0-9]{20,}"),
         "Stripe API Key"),
        (re.compile(r"xox[baprs]-[a-zA-Z0-9]{10,}"),
         "Slack Token"),
        (re.compile(r"(?i)gh[rsu]_[a-zA-Z0-9]{36,}"),
         "GitHub Token"),
    ]

    def __init__(self) -> None:
        self._intercept_log: list[dict[str, Any]] = []

    @property
    def intercept_log(self) -> list[dict[str, Any]]:
        return self._intercept_log

    async def run(self, ctx: Any, req: dict[str, Any], handle: Any) -> FilterResult:
        """Run the secret redaction filter.

        Scans request content for secrets and masks them before passing
        to the next handler. Non-destructive — the masked content is
        safe for LLM processing and database storage.

        Args:
            ctx: Agent context.
            req: Request dict with "content" or "text" field.
            handle: Next handler in the filter chain.

        Returns:
            FilterResult with masked content.
        """
        content = req.get("content", req.get("text", ""))

        if not content:
            return await handle()

        # Detect and mask secrets
        masked_content = content
        detected_count = 0

        for pattern, label in self.SECRET_PATTERNS:
            matches = pattern.findall(masked_content)
            if matches:
                detected_count += len(matches)
                masked_content = pattern.sub(
                    lambda m: self._mask_matched(m, label),
                    masked_content,
                )

        if detected_count > 0:
            self._log_intercept(
                "secret", "redact",
                f"脱敏 {detected_count} 个敏感信息",
            )

        req["content"] = masked_content
        req["text"] = masked_content

        result = await handle()
        result.content = masked_content
        return result

    def _mask_matched(self, match: re.Match, label: str) -> str:
        """Replace matched secret with a masked version.

        For key=value pairs, keeps the key name for context.
        """
        full = match.group()
        if "=" in full:
            key, _ = full.split("=", 1)
            return f"{key}=***  # {label}"
        if ":" in full:
            key, _ = full.split(":", 1)
            return f"{key}: ***  # {label}"
        if "://" in full:
            # Keep protocol and host, mask password
            return full.split("@")[0] + "@***:" + full.split(":")[-1]
        return f"***  # {label}"

    def _log_intercept(self, filter_type: str, action: str, reason: str) -> None:
        """Record an intercept event for later storage."""
        self._intercept_log.append({
            "filter_type": filter_type,
            "action": action,
            "target": "secret_detection",
            "reason": reason,
        })