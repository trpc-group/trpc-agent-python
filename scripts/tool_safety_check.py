#!/usr/bin/env python
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI for statically scanning tool scripts before execution."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety._audit import write_audit_event


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Scan Python or Bash tool scripts without executing them.")
    parser.add_argument("path", nargs="?", help="Path to script file to scan.")
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument("--script", help="Inline script text to scan.")
    input_group.add_argument("--file", help="Path to script file to scan.")
    parser.add_argument("--language", help="Script language: python, bash, or unknown.")
    parser.add_argument("--policy", help="Path to YAML safety policy.")
    parser.add_argument("--tool-name", default="tool_safety_check", help="Tool name used in the report.")
    parser.add_argument("--cwd", default="", help="Working directory to include in the scan request.")
    parser.add_argument("--audit-log", help="Path to append JSONL audit events.")
    parser.add_argument("--output", help="Path to write the JSON report.")
    parser.add_argument("--format", default="json", choices=["json"], help="Output format.")
    parser.add_argument("--block-on-review", action="store_true", help="Treat needs_human_review as blocked.")
    parser.add_argument("--strict-policy", action="store_true", help="Fail on invalid or unknown policy fields.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the CLI."""
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.path and (args.file or args.script):
            parser.error("positional path cannot be used with --file or --script")
        if not args.path and not args.file and args.script is None:
            parser.error("one of path, --file, or --script is required")

        policy = (
            ToolSafetyPolicy.from_file(args.policy, strict=args.strict_policy)
            if args.policy
            else ToolSafetyPolicy.default()
        )
        if args.block_on_review:
            policy.block_on_review = True
        scanner = ToolScriptSafetyScanner(policy)
        file_path = args.file or args.path

        if file_path:
            language = args.language or scanner.infer_language(file_path)
            report = scanner.scan_file(file_path, language=language, cwd=args.cwd, tool_name=args.tool_name)
        else:
            language = args.language or "unknown"
            report = scanner.scan_script(args.script, language, cwd=args.cwd, tool_name=args.tool_name)

        if args.audit_log:
            write_audit_event(report, args.audit_log)

        report_json = json.dumps(report.to_dict(), indent=2, sort_keys=True)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(report_json + "\n", encoding="utf-8")
        else:
            print(report_json)

        if report.decision == Decision.ALLOW:
            return 0
        if report.decision == Decision.NEEDS_HUMAN_REVIEW:
            return 2
        if report.decision == Decision.DENY:
            return 3
        return 1
    except SystemExit:
        raise
    except Exception as exc:  # pylint: disable=broad-except
        print(f"tool_safety_check error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
