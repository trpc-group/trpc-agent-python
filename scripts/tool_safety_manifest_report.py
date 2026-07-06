#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Validate tool safety samples from a manifest and write a deterministic report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trpc_agent_sdk.tools.safety import ToolSafetyPolicy
from trpc_agent_sdk.tools.safety import ToolScriptSafetyScanner
from trpc_agent_sdk.tools.safety import write_audit_event

EXAMPLE_DIR = REPO_ROOT / "examples" / "tool_safety"
DEFAULT_MANIFEST = EXAMPLE_DIR / "samples" / "manifest.yaml"
DEFAULT_POLICY = EXAMPLE_DIR / "tool_safety_policy.yaml"
DEFAULT_OUTPUT = EXAMPLE_DIR / "all_reports.json"
FIXED_TIMESTAMP = "1970-01-01T00:00:00+00:00"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate tool safety sample manifest.")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="Path to samples manifest.yaml.")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY), help="Path to tool_safety_policy.yaml.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Path to write deterministic JSON report.")
    parser.add_argument("--audit-log", help="Optional JSONL audit log path.")
    parser.add_argument("--strict-policy", action="store_true", help="Reject unknown or invalid policy fields.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    manifest_path = Path(args.manifest)
    samples_dir = manifest_path.parent
    manifest = _load_manifest(manifest_path)
    policy = ToolSafetyPolicy.from_file(args.policy, strict=args.strict_policy)
    scanner = ToolScriptSafetyScanner(policy)

    reports: list[dict[str, Any]] = []
    mismatches: list[str] = []
    decision_matches = 0
    required_rule_matches = 0

    for index, sample in enumerate(manifest["samples"], start=1):
        path = samples_dir / sample["file"]
        report = scanner.scan_file(path, language=_language_for(path), tool_name=sample["file"])
        report = _normalize_report(report, sample["file"], index)
        if args.audit_log:
            write_audit_event(args.audit_log, report)

        payload = report.to_dict()
        payload["sample"] = str(path.relative_to(REPO_ROOT))
        payload["expected_decision"] = sample["expected_decision"]
        payload["required_rule_ids"] = list(sample.get("required_rule_ids", []))
        payload["categories"] = list(sample.get("categories", []))
        reports.append(payload)

        if report.decision.value == sample["expected_decision"]:
            decision_matches += 1
        else:
            mismatches.append(
                f"{sample['file']}: expected decision {sample['expected_decision']}, got {report.decision.value}")

        actual_rules = {finding.rule_id for finding in report.findings}
        required_rules = set(sample.get("required_rule_ids", []))
        missing_rules = sorted(required_rules - actual_rules)
        if not missing_rules:
            required_rule_matches += 1
        else:
            mismatches.append(f"{sample['file']}: missing required rule(s): {', '.join(missing_rules)}")

    decisions = {
        decision: sum(1 for report in reports if report["decision"] == decision)
        for decision in ("allow", "deny", "needs_human_review")
    }
    category_checks = _category_checks(reports)
    summary = {
        "sample_count": len(reports),
        "decision_matches": decision_matches,
        "required_rule_matches": required_rule_matches,
        "decisions": decisions,
        "critical_category_checks": category_checks,
        "passed": not mismatches and all(category_checks.values()),
    }
    payload = {
        "generated_at": FIXED_TIMESTAMP,
        "summary": summary,
        "mismatches": mismatches,
        "reports": reports,
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    if mismatches:
        print("\n".join(mismatches), file=sys.stderr)
    return 0 if summary["passed"] else 1


def _load_manifest(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    samples = data.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Manifest must contain a non-empty samples list.")
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("Each manifest sample must be a mapping.")
        if not sample.get("file") or sample.get("expected_decision") not in {
                "allow",
                "deny",
                "needs_human_review",
        }:
            raise ValueError(f"Invalid manifest sample: {sample!r}")
    return data


def _language_for(path: Path) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix == ".sh":
        return "bash"
    return "unknown"


def _normalize_report(report, sample_name: str, index: int):
    report.scan_id = f"manifest:{index:03d}:{sample_name}"
    report.timestamp = FIXED_TIMESTAMP
    report.elapsed_ms = 0.0
    report.telemetry_attributes["tool.safety.scan_id"] = report.scan_id
    report.telemetry_attributes["tool.safety.duration_ms"] = 0.0
    return report


def _category_checks(reports: list[dict[str, Any]]) -> dict[str, bool]:
    checks = {
        "secret_read_no_allow": True,
        "dangerous_delete_no_allow": True,
        "non_whitelisted_network_no_allow": True,
        "safe_no_deny": True,
    }
    for report in reports:
        categories = set(report.get("categories", []))
        if "secret_read" in categories and report["decision"] == "allow":
            checks["secret_read_no_allow"] = False
        if "dangerous_delete" in categories and report["decision"] == "allow":
            checks["dangerous_delete_no_allow"] = False
        if "non_whitelisted_network" in categories and report["decision"] == "allow":
            checks["non_whitelisted_network_no_allow"] = False
        if "safe" in categories and report["decision"] == "deny":
            checks["safe_no_deny"] = False
    return checks


if __name__ == "__main__":
    raise SystemExit(main())
