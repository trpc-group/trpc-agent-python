# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI: scan a single script file and print a structured report.

Usage:
    python scripts/tool_safety_check.py path/to/script.py [--policy p.yaml] [--lang python]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from trpc_agent_sdk.tools.safety import load_policy
from trpc_agent_sdk.tools.safety import scan


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan a script for safety risks.")
    parser.add_argument("path", help="Path to the script file.")
    parser.add_argument("--policy", default=None, help="Path to a tool_safety_policy.yaml.")
    parser.add_argument("--lang", default="auto", help="python | bash | auto.")
    args = parser.parse_args()

    script = Path(args.path).read_text(encoding="utf-8")
    policy = load_policy(args.policy)
    report = scan(policy, script, language=args.lang)

    out = {
        "decision": report.decision.name,
        "risk_level": report.risk_level.name,
        "scan_duration_ms": report.scan_duration_ms,
        "findings": [
            {
                "rule_id": f.rule_id,
                "risk_level": f.risk_level.name,
                "decision": f.rule_decision.name,
                "evidence": f.evidence,
                "recommendation": f.recommendation,
            }
            for f in report.findings
        ],
        "recommendation": report.recommendation,
    }
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if report.decision.name == "ALLOW" else 1


if __name__ == "__main__":
    raise SystemExit(main())
