#!/usr/bin/env python3
"""Tool Script Safety Check — CLI for scanning scripts for security risks.

Usage:
    python scripts/tool_safety_check.py path/to/script.py
    python scripts/tool_safety_check.py path/to/script.sh --type bash
    python scripts/tool_safety_check.py --stdin < script.sh
    python scripts/tool_safety_check.py --version
"""

import argparse
import os
import sys
import json

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trpc_agent_sdk.tools.safety._policy import SafetyPolicy
from trpc_agent_sdk.tools.safety._scanner import SafetyScanner
from trpc_agent_sdk.tools.safety._types import ScanInput, ScriptType


def detect_script_type(path: str) -> ScriptType:
    """Detect script type from file extension."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".py", ):
        return ScriptType.PYTHON
    if ext in (".sh", ".bash", ".zsh", ".ksh"):
        return ScriptType.BASH
    return ScriptType.UNKNOWN


def main():
    parser = argparse.ArgumentParser(description="Tool Script Safety Check — scan scripts for security risks", )
    parser.add_argument("path", nargs="?", help="Path to script file")
    parser.add_argument("--type",
                        "-t",
                        choices=["auto", "bash", "python"],
                        default="auto",
                        help="Script type (default: auto-detect)")
    parser.add_argument("--stdin", action="store_true", help="Read script from stdin")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--policy", "-p", default="tool_safety_policy.yaml", help="Path to policy file")
    parser.add_argument("--version", "-v", action="store_true", help="Show version")

    args = parser.parse_args()

    if args.version:
        from trpc_agent_sdk.version import __version__ as ver
        print(f"tool-safety-check version {ver}")
        return

    # Read script content
    if args.stdin:
        script_content = sys.stdin.read()
        tool_name = "stdin"
        script_type = ScriptType.UNKNOWN
    elif args.path:
        path = args.path
        if not os.path.exists(path):
            print(f"Error: File not found: {path}", file=sys.stderr)
            sys.exit(1)
        with open(path, "r", encoding="utf-8") as f:
            script_content = f.read()
        tool_name = os.path.basename(path)
        script_type = detect_script_type(path) if args.type == "auto" \
            else ScriptType.PYTHON if args.type == "python" else ScriptType.BASH
    else:
        parser.print_help()
        sys.exit(1)

    # Load policy and scan
    try:
        policy = SafetyPolicy.from_file(args.policy)
    except FileNotFoundError:
        print(f"Error: Policy file not found: {args.policy}", file=sys.stderr)
        sys.exit(1)

    scanner = SafetyScanner(policy)
    scan_input = ScanInput(
        script_content=script_content,
        script_type=script_type,
        tool_name=tool_name,
    )
    report = scanner.scan(scan_input)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    else:
        _print_report(report)
        if report.is_blocked:
            sys.exit(2)
        elif report.needs_review:
            sys.exit(1)


def _print_report(report):
    """Print a human-readable safety report."""
    print(f"\n{'='*60}")
    print(f"  Tool Script Safety Report")
    print(f"{'='*60}")
    print(f"  Tool:         {report.tool_name}")
    print(f"  Script Type:  {report.script_type.name}")
    print(f"  Decision:     {report.decision.name}")
    print(f"  Risk Level:   {report.risk_level.name}")
    print(f"  Duration:     {report.scan_duration_ms:.2f}ms")
    print(f"  Matches:      {report.match_count}")
    print(f"  Timestamp:    {report.timestamp}")

    if report.matches:
        print(f"\n  {'─'*58}")
        for m in report.matches:
            print(f"  [{m.rule_id}] {m.risk_category.name} (risk={m.risk_level.name})")
            print(f"    Line {m.line_number}: {m.evidence[:100]}")
            print(f"    → {m.recommendation}")
            if m.masked:
                print(f"    (sensitive data masked)")
            print()
    else:
        print(f"\n  ✅ No risks detected.\n")


if __name__ == "__main__":
    main()
