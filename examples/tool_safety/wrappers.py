# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Safety wrapper examples for execution paths that do not use filters.

These wrappers intentionally live under ``examples``. They demonstrate how to
reuse the same deterministic ``SafetyReviewer`` used by ``ToolSafetyFilter``
without changing the core CodeExecutor or Skill implementations.
"""

from __future__ import annotations

import inspect
import json
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Mapping
from typing import Protocol
from typing import Sequence

from pydantic import Field

from trpc_agent_sdk._tool_safety_telemetry import trace_tool_safety_review
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools.safety import SafetyReview
from trpc_agent_sdk.tools.safety import SafetyReviewer
from trpc_agent_sdk.types import Outcome

_DEFAULT_BLOCK_DECISIONS = ("deny", "needs_human_review")


class SkillRunner(Protocol):
    """Minimal protocol for skill-like execution entries."""

    async def run_async(self, *, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        """Run the skill entry."""


SkillCallable = Callable[[InvocationContext, dict[str, Any]], Awaitable[Any] | Any]


class SafetyReviewedCodeExecutor(BaseCodeExecutor):
    """Composition wrapper that reviews code before delegating execution."""

    executor: BaseCodeExecutor = Field(exclude=True)
    reviewer: SafetyReviewer = Field(default_factory=SafetyReviewer, exclude=True)
    block_decisions: tuple[str, ...] = _DEFAULT_BLOCK_DECISIONS

    def __init__(
        self,
        executor: BaseCodeExecutor,
        *,
        reviewer: SafetyReviewer | None = None,
        block_decisions: Sequence[str] = _DEFAULT_BLOCK_DECISIONS,
    ) -> None:
        super().__init__(
            executor=executor,
            reviewer=reviewer or SafetyReviewer(),
            block_decisions=tuple(block_decisions),
            optimize_data_file=executor.optimize_data_file,
            stateful=executor.stateful,
            error_retry_attempts=executor.error_retry_attempts,
            execute_once_per_invocation=executor.execute_once_per_invocation,
            code_block_delimiters=list(executor.code_block_delimiters),
            execution_result_delimiters=list(executor.execution_result_delimiters),
            workspace_runtime=executor.workspace_runtime,
            ignore_codes=list(executor.ignore_codes),
        )

    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        """Review the code input and execute only when allowed."""
        review = self.reviewer.review(
            _serialize_code_execution_input(code_execution_input),
            action_type=_infer_code_action_type(code_execution_input),
            tool_name="code_executor",
        )
        trace_tool_safety_review(review)
        if review.decision not in self.block_decisions:
            return await self.executor.execute_code(invocation_context, code_execution_input)
        return _blocked_code_execution_result(review)


class SafetyReviewedSkillRunner:
    """Wrapper for direct skill execution entries that do not use filters."""

    def __init__(
        self,
        runner: SkillRunner | SkillCallable,
        *,
        reviewer: SafetyReviewer | None = None,
        block_decisions: Sequence[str] = _DEFAULT_BLOCK_DECISIONS,
        tool_name: str = "skill_run",
    ) -> None:
        self._runner = runner
        self._reviewer = reviewer or SafetyReviewer()
        self._block_decisions = frozenset(block_decisions)
        self._tool_name = tool_name

    @property
    def reviewer(self) -> SafetyReviewer:
        """Return the reviewer used by this wrapper."""
        return self._reviewer

    async def run(self, tool_context: InvocationContext, args: dict[str, Any]) -> Any:
        """Review skill arguments and execute only when allowed."""
        review = self._reviewer.review(
            _serialize_mapping(args),
            action_type=_infer_skill_action_type(args),
            tool_name=self._tool_name,
        )
        trace_tool_safety_review(review)
        if review.decision in self._block_decisions:
            return _blocked_skill_response(review)
        return await _call_skill_runner(self._runner, tool_context, args)


def _serialize_code_execution_input(code_execution_input: CodeExecutionInput) -> str:
    parts: list[str] = []
    for block in _code_blocks(code_execution_input):
        if block.language:
            parts.append(f"language: {block.language}")
        parts.append(block.code)
    if code_execution_input.input_files:
        parts.append(_serialize_mapping({"input_files": code_execution_input.input_files}))
    return "\n".join(part for part in parts if part)


def _code_blocks(code_execution_input: CodeExecutionInput) -> list[CodeBlock]:
    if code_execution_input.code_blocks:
        return list(code_execution_input.code_blocks)
    if code_execution_input.code:
        return [CodeBlock(code=code_execution_input.code, language="python")]
    return []


def _infer_code_action_type(code_execution_input: CodeExecutionInput) -> str:
    languages = {block.language.strip().lower() for block in _code_blocks(code_execution_input) if block.language}
    if languages & {"bash", "sh", "shell", "zsh"}:
        return "bash"
    if languages & {"python", "py"}:
        return "python"
    return next(iter(languages), "code")


def _infer_skill_action_type(args: Mapping[str, Any]) -> str:
    command = args.get("command")
    if isinstance(command, str):
        return "bash"
    return "skill"


async def _call_skill_runner(
    runner: SkillRunner | SkillCallable,
    tool_context: InvocationContext,
    args: dict[str, Any],
) -> Any:
    if hasattr(runner, "run_async"):
        result = runner.run_async(tool_context=tool_context, args=args)  # type: ignore[attr-defined]
    else:
        result = runner(tool_context, args)  # type: ignore[operator]
    if inspect.isawaitable(result):
        return await result
    return result


def _serialize_mapping(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return str(value)


def _blocked_code_execution_result(review: SafetyReview) -> CodeExecutionResult:
    return CodeExecutionResult(
        outcome=Outcome.OUTCOME_FAILED,
        output=json.dumps(_blocked_response("CODE_EXECUTION", review), ensure_ascii=False, sort_keys=True),
    )


def _blocked_skill_response(review: SafetyReview) -> dict[str, Any]:
    return _blocked_response("SKILL_EXECUTION", review)


def _blocked_response(prefix: str, review: SafetyReview) -> dict[str, Any]:
    response: dict[str, Any] = {
        "success": False,
        "error": f"{prefix}_BLOCKED: {review.finding}",
        "safety": review.report,
        "safety_audit": review.audit,
    }
    if review.decision == "needs_human_review":
        response["human_review"] = {
            "required": True,
            "status": "pending",
            "finding": review.finding,
            "recommendation": review.report.get("recommendation", ""),
        }
    return response
