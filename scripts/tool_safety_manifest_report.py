#!/usr/bin/env python
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Generate manifest-driven tool safety sample reports without executing samples."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner


def build_parser() -> argparse.ArgumentParser:
    """Build CLI argument parser."""
    parser = argparse.ArgumentParser(description="Generate all_reports.json from tool safety samples.")
    parser.add_argument("--manifest", default="examples/tool_safety/samples/manifest.yaml")
    parser.add_argument("--samples-dir", default="examples/tool_safety/samples")
    parser.add_argument("--policy", default="examples/tool_safety/tool_safety_policy.yaml")
    parser.add_argument("--output", default="examples/tool_safety/all_reports.json")
    parser.add_argument("--strict-policy", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate the JSON report matrix."""
    args = build_parser().parse_args(argv)
    manifest_path = Path(args.manifest)
    samples_dir = Path(args.samples_dir)
    output_path = Path(args.output)
    try:
        matrix = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["samples"]
        policy = ToolSafetyPolicy.from_file(args.policy, strict=args.strict_policy)
    except Exception as exc:  # pylint: disable=broad-except
        print(f"tool_safety_manifest_report error: {exc}", file=sys.stderr)
        return 1
    scanner = ToolScriptSafetyScanner(policy)

    reports = []
    failures = []
    matched_decisions = 0
    required_rules_present = 0
    for sample in matrix:
        report = scanner.scan_file(str(samples_dir / sample["file"]), language=sample["language"])
        rule_ids = {finding.rule_id for finding in report.findings}
        actual_decision = report.decision.value
        required_rule = sample["required_rule_id"]
        required_present = required_rule == "NONE" or required_rule in rule_ids
        expected_decision = sample["expected_decision"]
        matched_decision = actual_decision == expected_decision
        matched_decisions += int(matched_decision)
        required_rules_present += int(required_present)
        if not matched_decision or not required_present:
            failures.append(
                {
                    "file": sample["file"],
                    "expected_decision": expected_decision,
                    "actual_decision": actual_decision,
                    "required_rule_id": required_rule,
                    "actual_rule_ids": sorted(rule_ids),
                }
            )
        reports.append(
            {
                "file": sample["file"],
                "language": sample["language"],
                "expected_decision": expected_decision,
                "actual_decision": actual_decision,
                "required_rule_id": required_rule,
                "required_rule_present": required_present,
                "actual_rule_ids": sorted(rule_ids),
                "category": sample["category"],
                "high_risk": sample["high_risk"],
                "report": _stable_report_dict(report.to_dict(), sample["file"]),
            }
        )

    output = {
        "matched_decisions": matched_decisions,
        "reports": reports,
        "required_rules_present": required_rules_present,
        "sample_count": len(matrix),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("sample_count", "matched_decisions", "required_rules_present")}))
    if failures:
        print("manifest validation failures:")
        for failure in failures:
            print(
                "  "
                f"file={failure['file']} "
                f"expected_decision={failure['expected_decision']} "
                f"actual_decision={failure['actual_decision']} "
                f"required_rule_id={failure['required_rule_id']} "
                f"actual_rule_ids={','.join(failure['actual_rule_ids']) or 'NONE'}"
            )
        return 1
    if matched_decisions != len(matrix) or required_rules_present != len(matrix):
        return 1
    return 0


def _stable_report_dict(report: dict, file_name: str) -> dict:
    """Normalize dynamic report fields for reproducible manifest artifacts."""
    scan_id = f"manifest:{file_name}"
    report["scan_id"] = scan_id
    report["timestamp"] = "1970-01-01T00:00:00+00:00"
    report["elapsed_ms"] = 0.0
    telemetry = report.get("telemetry_attributes", {})
    telemetry["tool.safety.scan_id"] = scan_id
    telemetry["tool.safety.duration_ms"] = 0.0
    return report


if __name__ == "__main__":
    raise SystemExit(main())
