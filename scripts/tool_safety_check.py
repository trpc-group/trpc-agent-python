#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI for scanning tool scripts before execution."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import write_audit_event


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan Python or Bash tool scripts before execution.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--script", help="Path to the script or command file to scan, or '-' for stdin.")
    source_group.add_argument("--samples", help="Directory of sample scripts to scan as a batch.")
    parser.add_argument("--language", choices=["python", "bash", "sh", "shell", "unknown"], help="Script language.")
    parser.add_argument("--policy", help="Path to tool_safety_policy.yaml.")
    parser.add_argument("--strict-policy", action="store_true", help="Reject unknown or invalid policy fields.")
    parser.add_argument("--tool-name", default="tool_safety_cli", help="Tool name recorded in reports and audit logs.")
    parser.add_argument("--cwd", default="", help="Working directory that would be used for execution.")
    parser.add_argument("--output", help="Optional path to write the JSON report.")
    parser.add_argument("--audit-log", help="Optional JSONL audit log path.")
    parser.add_argument("--command-args", help="Command-line arguments that would be executed, parsed with shlex.")
    parser.add_argument("--timeout", type=float, help="Requested execution timeout in seconds.")
    parser.add_argument("--max-output-bytes", type=int, help="Requested maximum output size in bytes.")
    parser.add_argument(
        "--include-env",
        action="store_true",
        help="Include current environment keys in the scan context.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    policy = (ToolSafetyPolicy.from_file(args.policy, strict=args.strict_policy)
              if args.policy else ToolSafetyPolicy.default())
    scanner = ToolScriptSafetyScanner(policy)
    env = dict(os.environ) if args.include_env else {}
    tool_metadata = {}
    if args.timeout is not None:
        tool_metadata["timeout"] = args.timeout
    if args.max_output_bytes is not None:
        tool_metadata["max_output_bytes"] = args.max_output_bytes
    command_args = shlex.split(args.command_args or "")
    if args.samples:
        reports = []
        for path in sorted(Path(args.samples).iterdir()):
            if not path.is_file():
                continue
            report = scanner.scan_file(
                path,
                language=args.language,
                command_args=command_args,
                cwd=args.cwd,
                env=env,
                tool_name=path.name,
                tool_metadata=tool_metadata,
            )
            if args.audit_log:
                write_audit_event(args.audit_log, report)
            payload = report.to_dict()
            payload["sample"] = str(path)
            reports.append(payload)
        decisions = {
            decision: sum(1 for report in reports if report["decision"] == decision)
            for decision in ("allow", "deny", "needs_human_review")
        }
        payload = {
            "sample_count": len(reports),
            "decisions": decisions,
            "reports": reports,
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.output:
            Path(args.output).write_text(rendered + "\n", encoding="utf-8")
        else:
            print(rendered)
        return 0 if decisions["deny"] == 0 and decisions["needs_human_review"] == 0 else 2

    if args.script == "-":
        report = scanner.scan_script(
            sys.stdin.read(),
            args.language or "unknown",
            command_args=command_args,
            cwd=args.cwd,
            env=env,
            tool_name=args.tool_name,
            tool_metadata=tool_metadata,
        )
    else:
        report = scanner.scan_file(
            Path(args.script),
            language=args.language,
            command_args=command_args,
            cwd=args.cwd,
            env=env,
            tool_name=args.tool_name,
            tool_metadata=tool_metadata,
        )
    payload = report.to_dict()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    if args.audit_log:
        write_audit_event(args.audit_log, report)
    return 0 if report.decision.value == "allow" else 2


if __name__ == "__main__":
    sys.exit(main())
