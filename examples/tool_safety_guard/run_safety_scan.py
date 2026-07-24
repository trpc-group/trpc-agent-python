# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Generate the tool safety guard example report and audit log."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
repo_root = str(REPO_ROOT)
sys.path[:] = [path for path in sys.path if path != repo_root]
sys.path.insert(0, repo_root)

from trpc_agent_sdk.tools.safety._cli_helpers import FIXTURE_GENERATED_AT
from trpc_agent_sdk.tools.safety._cli_helpers import format_mismatches
from trpc_agent_sdk.tools.safety._cli_helpers import load_policy
from trpc_agent_sdk.tools.safety._cli_helpers import load_samples
from trpc_agent_sdk.tools.safety._cli_helpers import scan_samples
from trpc_agent_sdk.tools.safety._cli_helpers import write_audit_log
from trpc_agent_sdk.tools.safety._cli_helpers import write_json_report

EXAMPLE_DIR = Path(__file__).resolve().parent
POLICY_PATH = EXAMPLE_DIR / "tool_safety_policy.yaml"
SAMPLES_PATH = EXAMPLE_DIR / "samples.yaml"
REPORT_PATH = EXAMPLE_DIR / "tool_safety_report.json"
AUDIT_PATH = EXAMPLE_DIR / "tool_safety_audit.jsonl"


def main() -> int:
    report, audit_events, mismatches = scan_samples(
        load_samples(SAMPLES_PATH),
        load_policy(POLICY_PATH),
        generated_at=FIXTURE_GENERATED_AT,
        stable_elapsed_ms=0.0,
    )
    write_json_report(report, REPORT_PATH)
    write_audit_log(audit_events, AUDIT_PATH)

    print(f"Wrote report: {REPORT_PATH}")
    print(f"Wrote audit: {AUDIT_PATH}")
    print(f"Decision summary: {report['decision_summary']}")
    if mismatches:
        print(format_mismatches(mismatches), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
