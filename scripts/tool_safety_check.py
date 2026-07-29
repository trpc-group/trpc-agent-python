#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI safety scanner for CI/CD pipelines.

Exit codes:
  0 — allow
  1 — deny
  2 — needs_human_review
      (or 1 when --block-on-review is set)
  3 — usage error

Usage:
  python scripts/tool_safety_check.py script.sh
  python scripts/tool_safety_check.py --language python code.py
  echo "rm -rf /" | python scripts/tool_safety_check.py --stdin
  python scripts/tool_safety_check.py script.sh --policy my_policy.yaml --json
  python scripts/tool_safety_check.py --block-on-review script.sh
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk.tools.safety import AuditLogger
from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanRequest
from trpc_agent_sdk.tools.safety import normalize_language


def _exit_code_for_decision(decision: Decision, block_on_review: bool = False) -> int:
    """Return the semantic CLI exit code for a safety decision."""
    if decision == Decision.ALLOW:
        return 0
    if decision == Decision.DENY:
        return 1
    if decision == Decision.NEEDS_HUMAN_REVIEW:
        return 1 if block_on_review else 2
    return 3


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool Script Safety Check")
    parser.add_argument("file", nargs="?", help="Script file to scan")
    parser.add_argument("--stdin", action="store_true", help="Read script from stdin")
    parser.add_argument("--language", help="Script language (python / bash)")
    parser.add_argument("--policy", help="Path to YAML policy file")
    parser.add_argument("--audit-log", help="Path to JSONL audit log file")
    parser.add_argument("--block-on-review", action="store_true", help="Treat NEEDS_HUMAN_REVIEW decisions as blocked")
    parser.add_argument("--json", action="store_true", help="Output full report as JSON")
    args = parser.parse_args()

    # Resolve script content
    if args.stdin:
        script = sys.stdin.read()
    elif args.file:
        script = Path(args.file).read_text()
    else:
        parser.print_help()
        sys.exit(3)

    # Resolve language
    lang = args.language
    if not lang and args.file:
        suffix = Path(args.file).suffix.lower()
        lang = "python" if suffix == ".py" else "bash"
    lang = lang or "bash"

    # Load policy and scan
    policy = PolicyConfig.from_yaml(args.policy) if args.policy else PolicyConfig.default()
    scanner = SafetyScanner(policy)
    req = ScanRequest(script=script, language=normalize_language(lang), tool_name="cli_check")
    report = scanner.scan(req)
    if args.block_on_review and report.decision == Decision.NEEDS_HUMAN_REVIEW:
        report.set_blocked(True)

    # Audit log
    if args.audit_log:
        AuditLogger(args.audit_log).record(report)

    # Output
    if args.json:
        print(json.dumps(asdict(report), indent=2, ensure_ascii=False, default=str))
    else:
        print(f"decision: {report.decision.value}")
        print(f"risk_level: {report.risk_level.value}")
        print(f"summary: {report.summary}")
        for f in report.findings:
            print(f"  [{f.rule_id}] {f.risk_type}: {f.evidence[:80]}")

    # Exit with semantic code
    sys.exit(_exit_code_for_decision(report.decision, args.block_on_review))


if __name__ == "__main__":
    main()
