# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Run Tool Script Safety Guard against files or the public sample manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import yaml

from trpc_agent_sdk.tools.safety import JsonlAuditSink
from trpc_agent_sdk.tools.safety import SafetyAuditEvent
from trpc_agent_sdk.tools.safety import SafetyDecision
from trpc_agent_sdk.tools.safety import SafetyReport
from trpc_agent_sdk.tools.safety import SafetyScanRequest
from trpc_agent_sdk.tools.safety import SafetyScanner
from trpc_agent_sdk.tools.safety import ScriptLanguage

_EXIT_CODES = {
    SafetyDecision.ALLOW: 0,
    SafetyDecision.NEEDS_HUMAN_REVIEW: 2,
    SafetyDecision.DENY: 3,
}


def _language(path: Path, explicit: str | None = None) -> ScriptLanguage:
    if explicit:
        return ScriptLanguage(explicit)
    return ScriptLanguage.PYTHON if path.suffix == ".py" else ScriptLanguage.BASH


def _scan_file(
    scanner: SafetyScanner,
    path: Path,
    *,
    language: ScriptLanguage,
    cwd: str,
    tool_name: str,
) -> dict[str, Any]:
    report = scanner.scan(
        SafetyScanRequest(
            content=path.read_text(encoding="utf-8"),
            language=language,
            cwd=cwd,
            tool_name=tool_name,
            metadata={"sample_file": path.name},
        ))
    return {
        "file": path.name,
        "report": report.model_dump(mode="json"),
    }


def _manifest_entries(path: Path) -> list[dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("sample manifest must contain a YAML list")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="*", type=Path, help="Python or Bash files to scan")
    parser.add_argument("--manifest", type=Path, help="scan all files declared in a sample manifest")
    parser.add_argument("--policy", type=Path, help="YAML policy; built-in defaults are used when omitted")
    parser.add_argument("--language", choices=["python", "bash"], help="language for positional files")
    parser.add_argument("--cwd", default=str(Path.cwd()), help="execution working directory used for path rules")
    parser.add_argument("--tool-name", default="tool_safety_cli")
    parser.add_argument("--report", type=Path, help="write the combined JSON result")
    parser.add_argument("--audit", type=Path, help="append one sanitized JSONL audit event per file")
    parser.add_argument("--check-expected", action="store_true", help="validate manifest decisions and rule ids")
    args = parser.parse_args()

    if not args.files and args.manifest is None:
        parser.error("provide at least one file or --manifest")
    scanner = SafetyScanner.from_yaml(args.policy) if args.policy else SafetyScanner()
    jobs: list[tuple[Path, ScriptLanguage, dict[str, Any] | None]] = []
    if args.manifest:
        for entry in _manifest_entries(args.manifest):
            sample = args.manifest.parent / entry["file"]
            jobs.append((sample, _language(sample, entry.get("language")), entry))
    jobs.extend((path, _language(path, args.language), None) for path in args.files)

    results: list[dict[str, Any]] = []
    mismatches: list[str] = []
    for path, language, expected in jobs:
        item = _scan_file(
            scanner,
            path,
            language=language,
            cwd=args.cwd,
            tool_name=args.tool_name,
        )
        report = item["report"]
        results.append(item)
        if args.audit:
            event = SafetyAuditEvent.from_report(
                args.tool_name,
                SafetyReport.model_validate(report),
            )
            JsonlAuditSink(args.audit).emit(event)
        if args.check_expected and expected:
            actual_rules = {finding["rule_id"] for finding in report["findings"]}
            missing_rules = set(expected.get("expected_rule_ids", [])) - actual_rules
            if report["decision"] != expected["expected_decision"] or missing_rules:
                mismatches.append(f"{path.name}: decision={report['decision']}, missing_rules={sorted(missing_rules)}")

    payload = {"results": results}
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    if mismatches:
        print("Expected-result mismatches:", file=sys.stderr)
        for mismatch in mismatches:
            print(f"- {mismatch}", file=sys.stderr)
        return 1
    if args.check_expected:
        return 0
    return max((_EXIT_CODES[SafetyDecision(item["report"]["decision"])] for item in results), default=0)


if __name__ == "__main__":
    raise SystemExit(main())
