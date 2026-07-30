#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Safety wrapper for existing CodeExecutor implementations."""

from __future__ import annotations

import json

from pydantic import Field
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.code_executors import create_code_execution_result
from trpc_agent_sdk.context import InvocationContext
from typing_extensions import override

from ._filter import _blocked_response
from ._guard import ToolSafetyGuard
from ._models import ScriptLanguage
from ._models import ScriptPayload
from ._models import ScriptScanRequest
from ._models import ToolMetadata

_MIRRORED_FIELDS = (
    "optimize_data_file",
    "stateful",
    "error_retry_attempts",
    "execute_once_per_invocation",
    "code_block_delimiters",
    "execution_result_delimiters",
    "workspace_runtime",
    "ignore_codes",
)


class SafetyGuardedCodeExecutor(BaseCodeExecutor):
    """Compose one safety check in front of any CodeExecutor."""

    delegate: BaseCodeExecutor
    guard: ToolSafetyGuard = Field(default_factory=ToolSafetyGuard)
    tool_name: str = "code_executor"

    def __init__(self, **data):
        delegate = data.get("delegate")
        if delegate is not None:
            for field in _MIRRORED_FIELDS:
                data.setdefault(field, getattr(delegate, field))
        super().__init__(**data)

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        blocks = list(code_execution_input.code_blocks)
        if not blocks and code_execution_input.code:
            blocks = [CodeBlock(language="python", code=code_execution_input.code)]
        if not blocks:
            return await self.delegate.execute_code(invocation_context, code_execution_input)

        try:
            payloads = [
                ScriptPayload(
                    language=_code_language(block.language),
                    content=block.code,
                    source=f"code_blocks[{index}]",
                ) for index, block in enumerate(blocks)
            ]
            timeout = _delegate_timeout(self.delegate)
            request = ScriptScanRequest(
                payloads=payloads,
                metadata=ToolMetadata(
                    name=self.tool_name,
                    tool_type="code_executor",
                    tags=[type(self.delegate).__name__],
                ),
                cwd=str(getattr(self.delegate, "work_dir", "") or ""),
                env=_delegate_environment(self.delegate),
                requested_timeout=timeout if timeout is not None else 0,
                max_output_bytes=self.guard.scanner.policy.max_output_bytes,
            )
        except Exception as exc:  # pylint: disable=broad-except
            report = self.guard.scanner.failure_report(exc, rule_id="SCAN_INPUT_ERROR")
            report = self.guard.record(self.tool_name, report)
            payload = json.dumps(_blocked_response(report), ensure_ascii=False)
            return create_code_execution_result(stderr=payload)
        report = self.guard.check(request)
        if report.blocked:
            payload = json.dumps(_blocked_response(report), ensure_ascii=False)
            return create_code_execution_result(stderr=payload)

        result = await self.delegate.execute_code(invocation_context, code_execution_input)
        return _truncate_result(result, self.guard.scanner.policy.max_output_bytes)


def _code_language(language: str) -> ScriptLanguage:
    normalized = language.lower()
    if normalized in {"", "python", "py", "python3"}:
        return ScriptLanguage.PYTHON
    if normalized in {"bash", "sh", "shell"}:
        return ScriptLanguage.BASH
    raise ValueError(f"unsupported code block language: {language}")


def _delegate_timeout(delegate: BaseCodeExecutor) -> float | None:
    """Return the delegate's effective positive execution timeout."""

    timeout = getattr(delegate, "timeout", None)
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
        return float(timeout)
    config = getattr(delegate, "_cfg", None)
    timeout = getattr(config, "execute_timeout", None)
    if isinstance(timeout, (int, float)) and not isinstance(timeout, bool) and timeout > 0:
        return float(timeout)
    return None


def _delegate_environment(delegate: BaseCodeExecutor) -> dict[str, str]:
    """Copy execution environment values so they can be scanned and redacted."""

    environment = getattr(delegate, "environment", None)
    if environment is None:
        return {}
    if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in environment.items()):
        raise TypeError("delegate environment must map strings to strings")
    return dict(environment)


def _truncate_result(result: CodeExecutionResult, max_bytes: int) -> CodeExecutionResult:
    encoded = result.output.encode("utf-8")
    if len(encoded) <= max_bytes:
        return result
    marker = "\n[tool safety output truncated]"
    marker_bytes = marker.encode("utf-8")
    if max_bytes <= len(marker_bytes):
        result.output = marker_bytes[:max_bytes].decode("utf-8", errors="ignore")
        return result
    prefix = encoded[:max(0, max_bytes - len(marker_bytes))].decode("utf-8", errors="ignore")
    result.output = f"{prefix}{marker}"
    return result
