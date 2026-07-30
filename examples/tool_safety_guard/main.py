# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Batch-scan the 12 sample scripts and emit structured artifacts.

Run from this directory::

    python main.py

It scans every file under ``samples/`` with the example policy
(``tool_safety_policy.yaml``, falling back to the bundled default) and writes:

- ``tool_safety_report.json`` — one :class:`ScanReport` per sample
- ``tool_safety_audit.jsonl`` — one audit event per sample

It also prints a summary table so you can eyeball the verdicts at a glance.
"""

from __future__ import annotations

import json
from pathlib import Path

from trpc_agent_sdk.tools.safety import SafetyAuditLogger
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScanInput
from trpc_agent_sdk.tools.safety import ScriptLanguage
from trpc_agent_sdk.tools.safety import load_policy

_HERE = Path(__file__).resolve().parent
_SAMPLES_DIR = _HERE / "samples"
_POLICY_FILE = _HERE / "tool_safety_policy.yaml"
_REPORT_FILE = _HERE / "tool_safety_report.json"
_AUDIT_FILE = _HERE / "tool_safety_audit.jsonl"


def _language_for(path: Path) -> ScriptLanguage:
    """Infer language from the sample's file extension."""
    if path.suffix == ".py":
        return ScriptLanguage.PYTHON
    if path.suffix in (".sh", ".bash"):
        return ScriptLanguage.BASH
    return ScriptLanguage.UNKNOWN


def main() -> None:
    """Scan all samples and write the report + audit artifacts."""
    policy = load_policy(_POLICY_FILE) if _POLICY_FILE.exists() else load_policy(None)
    scanner = SafetyScanner(policy)

    # Fresh audit log each run.
    if _AUDIT_FILE.exists():
        _AUDIT_FILE.unlink()
    audit = SafetyAuditLogger(_AUDIT_FILE)

    reports = []
    print(f"{'sample':32s} {'decision':20s} {'risk':9s} rules")
    print("-" * 90)
    for path in sorted(_SAMPLES_DIR.glob("*")):
        if not path.is_file():
            continue
        scan_input = ScanInput(
            script=path.read_text(encoding="utf-8"),
            language=_language_for(path),
            tool_name=path.name,
        )
        report = scanner.scan(scan_input)
        audit.record(report, blocked=report.blocked)
        reports.append(report.model_dump(mode="json"))
        print(f"{path.name:32s} {report.decision.value:20s} "
              f"{report.risk_level.value:9s} {report.rule_ids()}")

    _REPORT_FILE.write_text(
        json.dumps(reports, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    total = len(reports)
    denied = sum(1 for r in reports if r["decision"] == "deny")
    review = sum(1 for r in reports if r["decision"] == "needs_human_review")
    allowed = sum(1 for r in reports if r["decision"] == "allow")
    print("-" * 90)
    print(f"scanned {total}: deny={denied}, needs_human_review={review}, allow={allowed}")
    print(f"report -> {_REPORT_FILE.name}")
    print(f"audit  -> {_AUDIT_FILE.name}")


if __name__ == "__main__":
    main()
