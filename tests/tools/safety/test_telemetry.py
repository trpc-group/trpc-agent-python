"""Tests for tool safety telemetry attributes."""

from __future__ import annotations

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest
from typing_extensions import override

from examples.tool_safety.wrappers import SafetyReviewedCodeExecutor
from examples.tool_safety.wrappers import SafetyReviewedSkillRunner
from trpc_agent_sdk.abc import FilterResult
from trpc_agent_sdk.code_executors import BaseCodeExecutor
from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.code_executors import CodeExecutionResult
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.types import Outcome


class RecordingCodeExecutor(BaseCodeExecutor):
    """Code executor that returns a fixed successful result."""

    @override
    async def execute_code(
        self,
        invocation_context: InvocationContext,
        code_execution_input: CodeExecutionInput,
    ) -> CodeExecutionResult:
        del invocation_context, code_execution_input
        return CodeExecutionResult(outcome=Outcome.OUTCOME_OK, output="executed")


class RecordingSkillTool:
    """Skill-like object that returns a fixed successful result."""

    async def run_async(self, *, tool_context: InvocationContext, args: dict) -> dict:
        del tool_context, args
        return {"success": True}


@pytest.mark.parametrize(
    ("args", "decision", "risk_level", "rule_id"),
    [
        ({
            "query": "hello"
        }, "allow", "none", "safe_python"),
        ({
            "command": "rm -rf /tmp/demo"
        }, "deny", "critical", "dangerous_delete"),
        ({
            "command": "npm install left-pad"
        }, "needs_human_review", "medium", "npm_install"),
    ],
)
async def test_tool_safety_filter_traces_review_attributes(args, decision, risk_level, rule_id) -> None:
    span = MagicMock()
    safety_filter = ToolSafetyFilter()

    with patch("trpc_agent_sdk._tool_safety_telemetry.trace.get_current_span", return_value=span), \
         patch("trpc_agent_sdk.tools.safety._filter.get_tool_var", return_value=MagicMock(name="demo_tool")):
        await safety_filter._before(MagicMock(), args, FilterResult())

    _assert_safety_attributes(span, decision, risk_level, rule_id)


@pytest.mark.parametrize(
    ("code_input", "decision", "risk_level", "rule_id"),
    [
        (CodeExecutionInput(code="print('hello')"), "allow", "none", "safe_python"),
        (
            CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="rm -rf /tmp/demo")]),
            "deny",
            "critical",
            "dangerous_delete",
        ),
        (
            CodeExecutionInput(code_blocks=[CodeBlock(language="bash", code="pip install unsafe-package")]),
            "needs_human_review",
            "medium",
            "package_install",
        ),
    ],
)
async def test_code_executor_wrapper_traces_review_attributes(code_input, decision, risk_level, rule_id) -> None:
    span = MagicMock()
    wrapper = SafetyReviewedCodeExecutor(RecordingCodeExecutor())

    with patch("trpc_agent_sdk._tool_safety_telemetry.trace.get_current_span", return_value=span):
        await wrapper.execute_code(MagicMock(), code_input)

    _assert_safety_attributes(span, decision, risk_level, rule_id)


@pytest.mark.parametrize(
    ("args", "decision", "risk_level", "rule_id"),
    [
        ({
            "skill": "demo",
            "command": "python scripts/run.py"
        }, "allow", "none", "safe_python"),
        ({
            "skill": "demo",
            "command": "rm -rf /tmp/demo"
        }, "deny", "critical", "dangerous_delete"),
        ({
            "skill": "demo",
            "command": "npm install left-pad"
        }, "needs_human_review", "medium", "npm_install"),
    ],
)
async def test_skill_wrapper_traces_review_attributes(args, decision, risk_level, rule_id) -> None:
    span = MagicMock()
    wrapper = SafetyReviewedSkillRunner(RecordingSkillTool())

    with patch("trpc_agent_sdk._tool_safety_telemetry.trace.get_current_span", return_value=span):
        await wrapper.run(MagicMock(), args)

    _assert_safety_attributes(span, decision, risk_level, rule_id)


async def test_tool_safety_telemetry_failure_does_not_block_tool_filter() -> None:
    span = MagicMock()
    span.set_attribute.side_effect = RuntimeError("otel disabled")
    safety_filter = ToolSafetyFilter()
    rsp = FilterResult()

    with patch("trpc_agent_sdk._tool_safety_telemetry.trace.get_current_span", return_value=span), \
         patch("trpc_agent_sdk.tools.safety._filter.get_tool_var", return_value=MagicMock(name="demo_tool")):
        await safety_filter._before(MagicMock(), {"query": "hello"}, rsp)

    assert rsp.rsp is None
    assert rsp.error is None
    assert rsp.is_continue is True


def _assert_safety_attributes(span: MagicMock, decision: str, risk_level: str, rule_id: str) -> None:
    span.set_attribute.assert_any_call("tool.safety.decision", decision)
    span.set_attribute.assert_any_call("tool.safety.risk_level", risk_level)
    span.set_attribute.assert_any_call("tool.safety.rule_id", rule_id)
