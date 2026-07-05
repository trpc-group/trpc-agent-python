#!/usr/bin/env python3
"""Run the tRPC-Agent tool script safety scanner from the command line."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trpc_agent_sdk.tools.safety import ToolSafetyGuard
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolSafetyScanRequest
from trpc_agent_sdk.tools.safety import SafetyDecision


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan Python or Bash tool scripts before execution.")
    parser.add_argument("script", help="Path to the script file to scan, or '-' to read stdin.")
    parser.add_argument("--language", "-l", default=None, help="Script language: python or bash.")
    parser.add_argument("--policy", "-p", default=None, help="Path to tool_safety_policy.yaml.")
    parser.add_argument("--cwd", default=None, help="Working directory that would be used for execution.")
    parser.add_argument("--tool-name", default="tool_safety_check", help="Tool name for report/audit metadata.")
    parser.add_argument("--report-out", default=None, help="Optional path to write the JSON report.")
    parser.add_argument("--audit-out", default=None, help="Optional path to append a JSONL audit event.")
    parser.add_argument("--allow-review", action="store_true", help="Exit 0 for needs_human_review decisions.")
    args = parser.parse_args()

    script_text = sys.stdin.read() if args.script == "-" else Path(args.script).read_text(encoding="utf-8")
    language = args.language or infer_language(args.script, script_text)
    policy = ToolSafetyPolicy.load(args.policy)
    guard = ToolSafetyGuard(policy=policy, audit_log_path=args.audit_out)
    report = guard.scan(
        ToolSafetyScanRequest(
            script=script_text,
            language=language,
            cwd=args.cwd,
            tool_metadata={"name": args.tool_name, "script_path": args.script},
        ))

    output = report.to_json(indent=2)
    if args.report_out:
        Path(args.report_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report_out).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)

    if report.decision == SafetyDecision.ALLOW:
        return 0
    if args.allow_review and report.decision == SafetyDecision.NEEDS_HUMAN_REVIEW:
        return 0
    return 2


def infer_language(path: str, script: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in {".sh", ".bash"}:
        return "bash"
    if script.lstrip().startswith(("#!/bin/bash", "#!/usr/bin/env bash", "#!/bin/sh")):
        return "bash"
    return "python"


if __name__ == "__main__":
    raise SystemExit(main())
