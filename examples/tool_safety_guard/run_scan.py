# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Batch-scan the 12 sample scripts and report acceptance metrics.

Produces ``tool_safety_report.json`` and ``tool_safety_audit.jsonl`` next to this
file, then prints the headline acceptance numbers:

- every sample scanned and reported,
- high-risk (deny) detection rate,
- safe-sample false-positive rate (safe -> deny only; review is not counted),
- 100% detection of the three must-catch categories,
- single 500-line scan duration.

Run::

    python examples/tool_safety_guard/run_scan.py
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import Language
from trpc_agent_sdk.tools.safety import AuditLogger
from trpc_agent_sdk.tools.safety import SafetyEngine
from trpc_agent_sdk.tools.safety import ScanInput
from trpc_agent_sdk.tools.safety import load_policy

HERE = Path(__file__).resolve().parent
SAMPLES_DIR = HERE / "samples"
POLICY_PATH = HERE / "tool_safety_policy.yaml"
REPORT_PATH = HERE / "tool_safety_report.json"
AUDIT_PATH = HERE / "tool_safety_audit.jsonl"

# Samples in the must-catch categories (must be denied 100% of the time).
MUST_CATCH = {
    "02_dangerous_delete.py": "dangerous_delete",
    "03_read_secret.py": "secret_read",
    "04_network_egress.py": "non_allowlisted_egress",
    "10_secret_leak_output.py": "secret_read",
    "11_curl_pipe_bash.sh": "non_allowlisted_egress",
}

_SUFFIX_LANG = {".py": Language.PYTHON, ".sh": Language.BASH, ".bash": Language.BASH}


def detect_language(path: Path) -> Language:
    return _SUFFIX_LANG.get(path.suffix.lower(), Language.UNKNOWN)


def main() -> int:
    expected = json.loads((SAMPLES_DIR / "EXPECTED.json").read_text(encoding="utf-8"))
    engine = SafetyEngine(load_policy(str(POLICY_PATH)))

    # Fresh audit log each run.
    if AUDIT_PATH.exists():
        AUDIT_PATH.unlink()
    audit = AuditLogger(str(AUDIT_PATH))

    reports: list[dict] = []
    correct = 0
    deny_total = deny_hit = 0
    safe_total = safe_fp = 0
    must_catch_hits: dict[str, bool] = {}

    for name in sorted(expected):
        path = SAMPLES_DIR / name
        script = path.read_text(encoding="utf-8", errors="ignore")
        report = engine.scan(ScanInput(script=script, tool_name=name, language=detect_language(path)))
        audit.log(report, blocked=report.decision == Decision.DENY)

        exp = expected[name]
        got = report.decision.value
        correct += int(got == exp)
        if exp == "deny":
            deny_total += 1
            deny_hit += int(got == "deny")
        if exp == "allow":
            safe_total += 1
            safe_fp += int(got == "deny")
        if name in MUST_CATCH:
            must_catch_hits[name] = (got == "deny")

        record = report.to_dict()
        record["file"] = name
        record["expected"] = exp
        reports.append(record)
        print(f"  [{got:>18}] expected={exp:<18} {name}")

    # 500-line performance probe.
    big = "\n".join(f"value_{i} = {i} * {i}" for i in range(500))
    start = time.perf_counter()
    engine.scan(ScanInput(script=big, tool_name="perf_probe", language=Language.PYTHON))
    duration = time.perf_counter() - start

    summary = {
        "total": len(reports),
        "decision_match": f"{correct}/{len(reports)}",
        "deny_detection_rate": f"{deny_hit}/{deny_total}",
        "safe_false_positive_rate": f"{safe_fp}/{safe_total}",
        "must_catch_all_denied": all(must_catch_hits.values()),
        "scan_500_lines_seconds": round(duration, 4),
    }
    REPORT_PATH.write_text(
        json.dumps({"summary": summary, "reports": reports}, ensure_ascii=False, indent=2),
        encoding="utf-8")

    print("\n=== acceptance metrics ===")
    print(f"all samples scanned & reported : {len(reports)}/12")
    print(f"decision match                 : {summary['decision_match']}")
    print(f"high-risk (deny) detection     : {summary['deny_detection_rate']}")
    print(f"safe false-positive (safe->deny): {summary['safe_false_positive_rate']}")
    print(f"must-catch categories 100%     : {summary['must_catch_all_denied']} {must_catch_hits}")
    print(f"500-line scan duration         : {summary['scan_500_lines_seconds']}s")
    print(f"\nreport: {REPORT_PATH}\naudit : {AUDIT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
