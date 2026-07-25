#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Batch scan 12 safety samples and produce report + audit artifacts.

Usage: python scripts/run_safety_scan.py [samples_dir]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk.tools.safety import PolicyConfig
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanRequest
from trpc_agent_sdk.tools.safety import normalize_language
from trpc_agent_sdk.tools.safety._audit import AuditLogger

EXPECTED = {
    # Original 12 samples
    "01": "allow",
    "02": "deny",
    "03": "deny",
    "04": "deny",
    "05": "allow",
    "06": "deny",
    "07": "deny",
    "08": "needs_human_review",
    "09": "needs_human_review",
    "10": "needs_human_review",
    "11": "deny",
    "12": "needs_human_review",
    # Extended samples (adversarial / edge cases)
    "13": "deny",                     # alias os.system
    "14": "deny",                     # from import subprocess.run
    "15": "needs_human_review",       # base64 pipe
    "16": "deny",                     # pathlib SSH access
    "17": "needs_human_review",       # requests.Session (medium import)
    "18": "allow",                    # os.getenv without exfil
    "19": "deny",                     # getattr builtins eval
    "20": "deny",                     # eval + exec
    "21": "deny",                     # find -delete
    "22": "deny",                     # xargs rm
    "23": "deny",                     # fork bomb
}


def main() -> None:
    samples_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("examples/tool_safety_guard/samples")
    out_dir = Path("examples/tool_safety_guard")
    audit_path = str(out_dir / "tool_safety_audit.jsonl")
    report_path = str(out_dir / "tool_safety_report.json")

    policy = PolicyConfig.default()
    # Use empty allowed_commands for demo — test safety rules, not command whitelist
    policy.allowed_commands = []
    scanner = SafetyScanner(policy)
    audit = AuditLogger(audit_path)

    results = []
    sample_files = sorted(samples_dir.glob("*"))

    for fpath in sample_files:
        label = fpath.stem
        script = fpath.read_text()
        lang = normalize_language("python" if fpath.suffix == ".py" else "bash")
        req = ScanRequest(script=script, language=lang, tool_name=label)
        report = scanner.scan(req)
        audit.record(report)

        expected = EXPECTED.get(label[:2], "allow")
        results.append({
            "label": label,
            "decision": report.decision.value,
            "risk_level": report.risk_level.value,
            "expected": expected,
            "match": report.decision.value == expected,
            "rule_ids": report.rule_ids,
            "findings_count": len(report.findings),
            "summary": report.summary,
        })
        status = "PASS" if report.decision.value == expected else "FAIL"
        print(f"[{status}] {label}: {report.decision.value} (expected {expected})"
              f" — {len(report.findings)} findings")

    with open(report_path, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    passed = sum(1 for r in results if r["match"])
    print(f"\nReport: {report_path}")
    print(f"Audit:  {audit_path}")
    print(f"Passed: {passed}/{len(results)}")


if __name__ == "__main__":
    main()
