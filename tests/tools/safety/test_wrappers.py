"""Tests for tool safety wrapper examples."""

from __future__ import annotations

import json
from typing_extensions import override
from unittest.mock import MagicMock

from examples.tool_safety.wrappers import SafetyReviewedCodeExecutor
from examples.tool_safety.wrappers import SafetyReviewedSkillRunner
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools.safety import SafetyReviewer
from trpc_agent_sdk.types import Outcome


class RecordingCodeExecutor(BaseCodeExecutor):
    """Code executor that records whether execution was delegated."""

    called: bool = False

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        del invocation_context, code_execution_input
        self.called = True
        return CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="executed")


class RecordingSkillTool:
    """Skill-like tool that records direct run_async calls."""

    def __init__(self) -> None:
        self.called = False

    async def run_async(self, *, tool_context: InvocationContext, args: dict) -> dict:
        del tool_context
        self.called = True
        return {"success": True, "args": args}


async def test_code_executor_wrapper_allows_safe_code() -> None:
    inner = RecordingCodeExecutor()
    wrapper = SafetyReviewedCodeExecutor(inner)

    result = await wrapper.execute_code(MagicMock(), CodeExecutionInput(code="print('hello')"))

    assert inner.called is True
    assert result.outcome == Outcome.OUTCOME_OK
    assert result.output == "executed"


async def test_code_executor_wrapper_blocks_deny_without_execution() -> None:
    inner = RecordingCodeExecutor()
    wrapper = SafetyReviewedCodeExecutor(inner)

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="rm -rf /tmp/demo")]),
    )

    payload = json.loads(result.output)
    assert inner.called is False
    assert result.outcome == Outcome.OUTCOME_FAILED
    assert payload["success"] is False
    assert payload["safety"]["decision"] == "deny"
    assert payload["safety"]["rule_id"] == "dangerous_delete"
    assert payload["safety_audit"]["tool_name"] == "code_executor"


async def test_code_executor_wrapper_returns_human_review_result() -> None:
    inner = RecordingCodeExecutor()
    wrapper = SafetyReviewedCodeExecutor(inner)

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="pip install unsafe-package")]),
    )

    payload = json.loads(result.output)
    assert inner.called is False
    assert payload["safety"]["decision"] == "needs_human_review"
    assert payload["safety"]["rule_id"] == "package_install"
    assert payload["human_review"]["required"] is True
    assert payload["human_review"]["status"] == "pending"


async def test_code_executor_wrapper_uses_provided_reviewer() -> None:
    reviewer = SafetyReviewer(allowed_domains=("api.example.com",))
    inner = RecordingCodeExecutor()
    wrapper = SafetyReviewedCodeExecutor(inner, reviewer=reviewer)

    result = await wrapper.execute_code(
        MagicMock(),
        CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="curl https://api.example.com/items")]),
    )

    assert wrapper.reviewer is reviewer
    assert inner.called is True
    assert result.outcome == Outcome.OUTCOME_OK


async def test_skill_wrapper_allows_safe_command() -> None:
    inner = RecordingSkillTool()
    wrapper = SafetyReviewedSkillRunner(inner)

    result = await wrapper.run(MagicMock(), {"skill": "demo", "command": "python scripts/run.py"})

    assert inner.called is True
    assert result == {"success": True, "args": {"skill": "demo", "command": "python scripts/run.py"}}


async def test_skill_wrapper_blocks_deny_without_running_skill() -> None:
    inner = RecordingSkillTool()
    wrapper = SafetyReviewedSkillRunner(inner)

    result = await wrapper.run(MagicMock(), {"skill": "demo", "command": "rm -rf /tmp/demo"})

    assert inner.called is False
    assert result["success"] is False
    assert result["safety"]["decision"] == "deny"
    assert result["safety"]["rule_id"] == "dangerous_delete"
    assert result["safety_audit"]["tool_name"] == "skill_run"


async def test_skill_wrapper_returns_human_review_result() -> None:
    inner = RecordingSkillTool()
    wrapper = SafetyReviewedSkillRunner(inner)

    result = await wrapper.run(MagicMock(), {"skill": "demo", "command": "npm install left-pad"})

    assert inner.called is False
    assert result["safety"]["decision"] == "needs_human_review"
    assert result["safety"]["rule_id"] == "npm_install"
    assert result["human_review"]["required"] is True
    assert result["human_review"]["status"] == "pending"


async def test_skill_wrapper_can_wrap_callable_and_reuse_reviewer() -> None:
    reviewer = SafetyReviewer(allowed_domains=("api.example.com",))
    called = False

    async def runner(tool_context: InvocationContext, args: dict) -> dict:
        nonlocal called
        del tool_context
        called = True
        return {"ok": args["url"]}

    wrapper = SafetyReviewedSkillRunner(runner, reviewer=reviewer, tool_name="custom_skill")

    result = await wrapper.run(MagicMock(), {"url": "https://api.example.com/data"})

    assert wrapper.reviewer is reviewer
    assert called is True
    assert result == {"ok": "https://api.example.com/data"}
