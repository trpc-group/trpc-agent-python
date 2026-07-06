# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the tool safety guard quickstart project.

This script demonstrates the same guard in three integration points:
direct scan, Tool Filter, and CodeExecutor wrapper. It writes a structured
summary under ``out/`` without executing any untrusted sample script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trpc_agent_sdk.code_executors import CodeBlock  # noqa: E402
from trpc_agent_sdk.code_executors import CodeExecutionInput  # noqa: E402
from trpc_agent_sdk.context import create_agent_context  # noqa: E402
from trpc_agent_sdk.filter import FilterResult  # noqa: E402
from trpc_agent_sdk.tools.safety import SafetyGuardedCodeExecutor  # noqa: E402
from trpc_agent_sdk.tools.safety import ToolSafetyFilter  # noqa: E402
from trpc_agent_sdk.tools.safety import ToolSafetyGuard  # noqa: E402
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy  # noqa: E402
from trpc_agent_sdk.tools.safety import ToolSafetyScanRequest  # noqa: E402

try:
    from examples.tool_safety_guard.quickstart.tool_service import DryRunScriptExecutor  # noqa: E402
    from examples.tool_safety_guard.quickstart.tool_service import dry_run_tool  # noqa: E402
    from examples.tool_safety_guard.quickstart.tool_service import read_script  # noqa: E402
except ModuleNotFoundError:
    from tool_service import DryRunScriptExecutor  # noqa: E402
    from tool_service import dry_run_tool  # noqa: E402
    from tool_service import read_script  # noqa: E402

DEFAULT_POLICY = HERE / "policy.yaml"
DEFAULT_OUT = HERE / "out"
DEFAULT_CASES = [
    ("safe_report", HERE / "scripts" / "safe_report.py", "python"),
    ("external_upload", HERE / "scripts" / "external_upload.py", "python"),
    ("read_secret", HERE / "scripts" / "read_secret.py", "python"),
    ("review_subprocess", HERE / "scripts" / "review_subprocess.py", "python"),
    ("dangerous_cleanup", HERE / "scripts" / "dangerous_cleanup.sh", "bash"),
]


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
            "cwd": str(HERE),
        },
        handle,
    )
    return {
        "continued": bool(result.is_continue),
        "response": result.rsp,
    }


async def _run_executor_case(guard: ToolSafetyGuard, script: str, language: str) -> dict[str, Any]:
    executor = SafetyGuardedCodeExecutor(
        delegate=DryRunScriptExecutor(work_dir=str(HERE)),
        guard=guard,
    )
    result = await executor.execute_code(
        invocation_context=None,  # type: ignore[arg-type]
        code_execution_input=CodeExecutionInput(code_blocks=[CodeBlock(language=language, code=script)]),
    )
    return {
        "outcome": result.outcome.value if hasattr(result.outcome, "value") else str(result.outcome),
        "output": result.output,
    }


async def run_quickstart(*, policy_path: Path = DEFAULT_POLICY, output_dir: Path = DEFAULT_OUT) -> dict[str, Any]:
    policy = ToolSafetyPolicy.load(policy_path)
    audit_path = output_dir / "audit.jsonl"
    guard = ToolSafetyGuard(policy=policy, audit_log_path=audit_path)
    cases = []

    for name, path, language in DEFAULT_CASES:
        script = read_script(path)
        report = guard.scan(
            ToolSafetyScanRequest(
                script=script,
                language=language,
                cwd=str(HERE),
                tool_metadata={
                    "name": name,
                    "script_path": str(path),
                },
            ))
        filter_result = await _run_filter_case(guard, script, language)
        executor_result = await _run_executor_case(guard, script, language)
        cases.append({
            "name": name,
            "script": str(path.relative_to(HERE)),
            "language": language,
            "decision": report.decision.value if hasattr(report.decision, "value") else str(report.decision),
            "risk_level": report.risk_level.value if hasattr(report.risk_level, "value") else str(report.risk_level),
            "blocked": report.blocked,
            "rule_ids": [finding.rule_id for finding in report.findings],
            "summary": report.summary,
            "filter": filter_result,
            "code_executor": executor_result,
        })

    summary = {
        "policy": {
            "name": policy.name,
            "version": policy.version,
            "path": str(policy_path),
        },
        "case_count": len(cases),
        "decision_counts": {
            decision: sum(1 for case in cases if case["decision"] == decision)
            for decision in sorted({case["decision"] for case in cases})
        },
        "cases": cases,
        "audit_log": str(audit_path),
    }
    _write_json(output_dir / "quickstart_report.json", summary)
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the tool safety guard quickstart.")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(run_quickstart(policy_path=args.policy.resolve(), output_dir=args.output_dir.resolve()))
    print("Tool safety quickstart decisions:")
    for case in summary["cases"]:
        print(f"- {case['name']}: {case['decision']} ({', '.join(case['rule_ids']) or 'no findings'})")
    print(f"Report written to: {summary['audit_log']}")


if __name__ == "__main__":
    main()
