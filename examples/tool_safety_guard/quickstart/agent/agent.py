# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tool safety guard quickstart application logic.

This module is shaped like other quickstart ``agent/agent.py`` files, but it is
model-free on purpose: the issue being demonstrated is pre-execution script
safety for tools and code executors, so the sample can run without API keys.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from trpc_agent_sdk.code_executors import CodeBlock
from trpc_agent_sdk.code_executors import CodeExecutionInput
from trpc_agent_sdk.context import create_agent_context
from trpc_agent_sdk.filter import FilterResult
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor
from trpc_agent_sdk.tools.safety import ToolSafetyFilter
from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanRequest

from .config import DEFAULT_CASES
from .config import DEFAULT_OUT
from .config import DEFAULT_POLICY
from .config import QUICKSTART_DIR
from .config import ScriptSafetyCase
from .tools import DryRunScriptExecutor
from .tools import dry_run_tool
from .tools import read_script


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def _run_filter_case(guard: ToolSafetyGuard, script: str, language: str) -> dict[str, Any]:
    filter_ = ToolSafetyFilter(guard=guard)

    async def handle() -> FilterResult:
        return FilterResult(rsp=await dry_run_tool(script, language=language))

    result = await filter_.run(
        create_agent_context(),
        {
            "script": script,
            "language": language,
            "cwd": str(QUICKSTART_DIR),
        },
        handle,
    )
    return {
        "continued": bool(result.is_continue),
        "response": result.rsp,
    }


async def _run_executor_case(guard: ToolSafetyGuard, script: str, language: str) -> dict[str, Any]:
    executor = SafetyGuardedCodeExecutor(
        delegate=DryRunScriptExecutor(work_dir=str(QUICKSTART_DIR)),
        guard=guard,
    )
    result = await executor.execute_code(
        invocation_context=None,  # type: ignore[arg-type]
        code_execution_input=CodeExecutionInput(code_blocks=[CodeBlock(language=language, code=script)]),
    )
    return {
        "outcome": _enum_value(result.outcome),
        "output": result.output,
    }


async def run_quickstart(
    *,
    policy_path: Path = DEFAULT_POLICY,
    output_dir: Path = DEFAULT_OUT,
    cases: Iterable[ScriptSafetyCase] = DEFAULT_CASES,
) -> dict[str, Any]:
    """Run the model-free tool safety quickstart and write a report."""

    policy = ToolSafetyPolicy.load(policy_path)
    audit_path = output_dir / "audit.jsonl"
    report_path = output_dir / "quickstart_report.json"
    guard = ToolSafetyGuard(policy=policy, audit_log_path=audit_path)
    case_results = []

    for case in cases:
        script = read_script(case.script_path)
        report = guard.scan(
            ToolSafetyScanRequest(
                script=script,
                language=case.language,
                cwd=str(QUICKSTART_DIR),
                tool_metadata={
                    "name": case.name,
                    "script_path": str(case.script_path),
                },
            )
        )
        filter_result = await _run_filter_case(guard, script, case.language)
        executor_result = await _run_executor_case(guard, script, case.language)
        case_results.append(
            {
                "name": case.name,
                "script": str(case.script_path.relative_to(QUICKSTART_DIR)),
                "language": case.language,
                "decision": _enum_value(report.decision),
                "risk_level": _enum_value(report.risk_level),
                "blocked": report.blocked,
                "rule_ids": [finding.rule_id for finding in report.findings],
                "summary": report.summary,
                "filter": filter_result,
                "code_executor": executor_result,
            }
        )

    summary = {
        "policy": {
            "name": policy.name,
            "version": policy.version,
            "path": str(policy_path),
        },
        "case_count": len(case_results),
        "decision_counts": {
            decision: sum(1 for case in case_results if case["decision"] == decision)
            for decision in sorted({case["decision"] for case in case_results})
        },
        "cases": case_results,
        "report": str(report_path),
        "audit_log": str(audit_path),
    }
    _write_json(report_path, summary)
    return summary
