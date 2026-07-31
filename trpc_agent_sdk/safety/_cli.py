# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Command-line entry point for static tool script safety checks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from typing import Optional

import yaml

from ._audit import JsonlAuditSink
from ._models import SafetyDecision
from ._models import SafetyReport
from ._models import SafetyScanRequest
from ._policy import PolicyLoader
from ._policy import SafetyPolicy
from ._scanner import SafetyScanner

EXIT_ALLOW = 0
EXIT_DENY = 2
EXIT_REVIEW = 3
EXIT_INVALID_INPUT = 4
EXIT_OPERATIONAL_FAILURE = 5
EXIT_MANIFEST_MISMATCH = 6


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Statically scan Python or Shell source without executing it.")
    parser.add_argument("file", nargs="?", help="Source file to scan; omit with --stdin.")
    parser.add_argument("--stdin", action="store_true", help="Read source from standard input.")
    parser.add_argument("--language", choices=("python", "shell", "bash", "sh"), help="Source language.")
    parser.add_argument("--policy", help="Strict YAML policy path.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Print a structured JSON report.")
    parser.add_argument("--audit", help="Append redacted observations to this JSONL path.")
    parser.add_argument("--manifest", help="Validate every sample in a YAML manifest.")
    return parser


def _language_for(path: Path, explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if path.suffix.lower() == ".py":
        return "python"
    if path.suffix.lower() in {".sh", ".bash"}:
        return "shell"
    raise ValueError("language is required for an unknown file extension")


def _exit_for(report: SafetyReport) -> int:
    if report.decision is SafetyDecision.ALLOW:
        return EXIT_ALLOW
    if report.decision is SafetyDecision.DENY:
        return EXIT_DENY
    return EXIT_REVIEW


def _print_report(report: SafetyReport, as_json: bool) -> None:
    if as_json:
        print(report.model_dump_json(exclude_none=True))
        return
    risk = report.risk_level.value if report.risk_level else "none"
    print(f"decision={report.decision.value} risk={risk} blocked={str(report.execution_blocked).lower()}")


def _scanner(policy_path: Optional[str], audit_path: Optional[str]) -> SafetyScanner:
    policy = PolicyLoader(policy_path).load() if policy_path else SafetyPolicy.default()
    audit = JsonlAuditSink(audit_path) if audit_path else None
    return SafetyScanner(policy, audit_sink=audit)


def _validate_manifest(path: Path, scanner: SafetyScanner, as_json: bool) -> int:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema_version") != "1" or not isinstance(raw.get("samples"), list):
        raise ValueError("invalid sample manifest")
    results: list[dict[str, Any]] = []
    mismatch = False
    for sample in raw["samples"]:
        if not isinstance(sample, dict):
            raise ValueError("invalid sample entry")
        sample_path = path.parent / str(sample["file"])
        language = _language_for(sample_path, sample.get("language"))
        report = scanner.scan(
            SafetyScanRequest(
                script=sample_path.read_text(encoding="utf-8"),
                language=language,
                source_type="public_sample",
                source_name=str(sample.get("name") or sample_path.name),
            ))
        required = set(sample.get("required_rules") or [])
        actual = set(report.rule_ids)
        risk = report.risk_level.value if report.risk_level else None
        matched = (report.decision.value == sample.get("expected_decision") and risk == sample.get("expected_risk")
                   and report.execution_blocked is bool(sample.get("expected_blocked")) and required.issubset(actual))
        mismatch = mismatch or not matched
        results.append({
            "name": sample.get("name"),
            "matched": matched,
            "decision": report.decision.value,
            "risk_level": risk,
            "blocked": report.execution_blocked,
            "rule_ids": list(report.rule_ids),
        })
    payload = {"schema_version": "1", "manifest": str(path), "matched": not mismatch, "results": results}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True))
    else:
        print(f"manifest_samples={len(results)} matched={str(not mismatch).lower()}")
    return EXIT_MANIFEST_MISMATCH if mismatch else EXIT_ALLOW


def main(argv: Optional[list[str]] = None) -> int:
    """Run the scanner CLI and return a stable exit code."""
    args = _parser().parse_args(argv)
    try:
        scanner = _scanner(args.policy, args.audit)
        if args.manifest:
            if args.file or args.stdin:
                raise ValueError("manifest mode cannot be combined with file or stdin")
            return _validate_manifest(Path(args.manifest), scanner, args.as_json)
        if bool(args.file) == bool(args.stdin):
            raise ValueError("provide exactly one file or --stdin")
        if args.stdin:
            if not args.language:
                raise ValueError("--language is required with --stdin")
            source = sys.stdin.read()
            language = args.language
            source_name = "stdin"
        else:
            path = Path(args.file)
            source = path.read_text(encoding="utf-8")
            language = _language_for(path, args.language)
            source_name = path.name
        report = scanner.scan(
            SafetyScanRequest(
                script=source,
                language=language,
                source_type="cli",
                source_name=source_name,
            ))
        _print_report(report, args.as_json)
        return _exit_for(report)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError):
        print("tool safety check failed: invalid input or policy", file=sys.stderr)
        return EXIT_INVALID_INPUT
    except Exception:  # pylint: disable=broad-except
        print("tool safety check failed: operational failure", file=sys.stderr)
        return EXIT_OPERATIONAL_FAILURE
