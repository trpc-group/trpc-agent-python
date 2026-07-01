#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0
"""tool_safety_check.py — CLI for the Tool Script Safety Guard.

Scan a single script or a directory of samples, emit a structured JSON report
and a JSONL audit log. Exits non-zero when any sample is DENIED.

Examples::

    # Scan one file
    python examples/tool_safety/tool_safety_check.py --script path/to/script.py

    # Scan the 12 samples and write report + audit files
    python examples/tool_safety/tool_safety_check.py \
        --samples examples/tool_safety/samples/ \
        --policy examples/tool_safety/tool_safety_policy.yaml \
        --report examples/tool_safety/tool_safety_report.json \
        --audit examples/tool_safety/tool_safety_audit.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def _ensure_import_path() -> None:
    """Make ``examples.tool_safety.safety`` importable when run from repo root."""
    here = Path(__file__).resolve()
    # examples/tool_safety/tool_safety_check.py -> repo root is 3 levels up.
    repo_root = here.parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def main(argv: Optional[list[str]] = None) -> int:
    _ensure_import_path()

    parser = argparse.ArgumentParser(description="Tool Script Safety Guard CLI")
    parser.add_argument("--script", help="Path to a single script to scan.")
    parser.add_argument("--samples", help="Directory of sample scripts to scan.")
    parser.add_argument(
        "--policy",
        default="examples/tool_safety/tool_safety_policy.yaml",
        help="Path to tool_safety_policy.yaml.",
    )
    parser.add_argument("--report", help="Path to write the JSON report.")
    parser.add_argument("--audit", help="Path to write the JSONL audit log.")
    parser.add_argument("--language", default="", help="Override language (python/bash).")
    parser.add_argument("--verbose", action="store_true", help="Print each finding.")
    args = parser.parse_args(argv)

    from examples.tool_safety.safety import PolicyConfig
    from examples.tool_safety.safety import SafetyScanner
    from examples.tool_safety.safety import ScanInput
    from examples.tool_safety.safety.audit import AuditLogger

    policy = PolicyConfig.from_yaml(args.policy)
    scanner = SafetyScanner(policy=policy)
    audit = AuditLogger(args.audit)

    targets: list[Path] = []
    if args.script:
        targets.append(Path(args.script))
    if args.samples:
        samples_dir = Path(args.samples)
        targets.extend(sorted(samples_dir.glob("*")))
    if not targets:
        parser.error("provide --script or --samples")

    all_reports: list[dict] = []
    any_denied = False

    for target in targets:
        if not target.is_file():
            continue
        script = target.read_text(encoding="utf-8")
        lang = args.language or _infer_language(target)
        scan_input = ScanInput(script=script, language=lang, tool_name=target.name)
        report = scanner.scan(scan_input)
        audit.log(report, script_path=str(target), intercepted=report.blocked)

        record = report.to_dict()
        record["script_path"] = str(target)
        all_reports.append(record)

        if report.decision.value == "deny":
            any_denied = True
        if args.verbose or report.decision.value != "allow":
            print(f"[{report.decision.value.upper():>20}] {target.name} "
                  f"(risk={report.risk_level.value}, rules={report.rule_ids})")
            for f in report.findings:
                print(f"    - {f.rule_id} L{f.line}: {f.evidence}")

    if args.report:
        Path(args.report).write_text(
            json.dumps({"reports": all_reports}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nReport written to {args.report}")
    if args.audit:
        print(f"Audit log appended to {args.audit}")

    # Summary
    allowed = sum(1 for r in all_reports if r["decision"] == "allow")
    denied = sum(1 for r in all_reports if r["decision"] == "deny")
    review = sum(1 for r in all_reports if r["decision"] == "needs_human_review")
    print(f"\nSummary: {len(all_reports)} scanned | "
          f"{allowed} allow | {denied} deny | {review} needs_review")

    return 1 if any_denied else 0


def _infer_language(path: Path) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix in (".sh", ".bash"):
        return "bash"
    return ""


if __name__ == "__main__":
    sys.exit(main())
