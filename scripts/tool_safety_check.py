#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standalone CLI for the Tool Script Safety Guard.

Scans a script file (or stdin) for safety risks and prints a structured
report.  Useful for CI pipelines, pre-commit hooks and manual review.

Usage:
    # Scan a file
    python scripts/tool_safety_check.py script.py

    # Scan a bash script
    python scripts/tool_safety_check.py deploy.sh --type bash

    # Scan from stdin
    cat suspicious.sh | python scripts/tool_safety_check.py -

    # Use a custom policy
    python scripts/tool_safety_check.py script.py --policy tool_safety_policy.yaml

    # Output JSON report to a file
    python scripts/tool_safety_check.py script.py --output report.json

    # Write audit log
    python scripts/tool_safety_check.py script.py --audit audit.jsonl

Exit codes:
    0 = allow (no risks found)
    1 = needs_human_review
    2 = deny (dangerous script)
    3 = error
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Tool Script Safety Guard — scan scripts for security risks before execution.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input",
        help="Script file to scan, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--type",
        "-t",
        choices=["python", "bash", "auto"],
        default="auto",
        help="Script type (default: auto-detect).",
    )
    parser.add_argument(
        "--policy",
        "-p",
        help="Path to a YAML policy file. Uses built-in defaults if omitted.",
    )
    parser.add_argument(
        "--tool-name",
        default="cli_check",
        help="Name of the tool that would execute the script (default: cli_check).",
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Write the JSON report to this file (default: stdout).",
    )
    parser.add_argument(
        "--audit",
        help="Append an audit event to this JSONL file.",
    )
    parser.add_argument(
        "--block-on-review",
        action="store_true",
        help="Exit with code 2 (deny) for needs_human_review results too.",
    )
    parser.add_argument(
        "--quiet",
        "-q",
        action="store_true",
        help="Only output the decision, no details.",
    )

    args = parser.parse_args()

    # --- Read input ---
    if args.input == "-":
        script = sys.stdin.read()
    else:
        path = Path(args.input)
        if not path.exists():
            print(f"Error: file not found: {path}", file=sys.stderr)
            return 3
        script = path.read_text(encoding="utf-8")

    # --- Import safety guard ---
    try:
        from trpc_agent_sdk.tools.safety import AuditLogger
        from trpc_agent_sdk.tools.safety import Decision
        from trpc_agent_sdk.tools.safety import SafetyGuard
    except ImportError as ex:
        print(f"Error: cannot import safety module: {ex}", file=sys.stderr)
        return 3

    # --- Build guard ---
    audit_logger = AuditLogger(path=args.audit) if args.audit else None
    if args.policy:
        guard = SafetyGuard.from_yaml(args.policy, audit_logger=audit_logger)
    else:
        guard = SafetyGuard.default(audit_logger=audit_logger)

    # --- Scan ---
    hint = None if args.type == "auto" else args.type
    report = guard.scan(
        script,
        tool_name=args.tool_name,
        script_type_hint=hint,
    )

    # --- Output ---
    if args.quiet:
        print(report.decision.value)
    else:
        report_json = report.to_json(indent=2)
        if args.output:
            Path(args.output).write_text(report_json, encoding="utf-8")
            print(f"Report written to {args.output}", file=sys.stderr)
        else:
            print(report_json)

        if not args.output:
            print(file=sys.stderr)
            print(f"Decision: {report.decision.value}", file=sys.stderr)
            print(f"Risk:     {report.risk_level.value}", file=sys.stderr)
            print(f"Scan ms:  {report.scan_duration_ms:.1f}", file=sys.stderr)
            if report.findings:
                print(f"Findings: {len(report.findings)}", file=sys.stderr)
                for f in report.findings:
                    print(f"  [{f.rule_id}] {f.description}", file=sys.stderr)
                    if f.line_number:
                        print(f"    Line {f.line_number}: {f.evidence}", file=sys.stderr)

    # --- Exit code ---
    if report.decision == Decision.ALLOW:
        return 0
    elif report.decision == Decision.DENY:
        return 2
    elif report.decision == Decision.NEEDS_HUMAN_REVIEW:
        return 2 if args.block_on_review else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
