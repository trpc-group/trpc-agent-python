#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI for the Tool Script Safety Guard.

Scan a single script or a directory of samples, emit a structured JSON report
and a JSONL audit log.

Exit codes:
  0 — all allow
  1 — at least one deny
  2 — no deny, but at least one needs_human_review

Examples::

    python scripts/tool_safety_check.py --script path/to/script.py

    python scripts/tool_safety_check.py \\
        --samples examples/tool_safety/samples/ \\
        --policy examples/tool_safety/tool_safety_policy.yaml \\
        --report examples/tool_safety/tool_safety_report.json \\
        --audit examples/tool_safety/tool_safety_audit.jsonl \\
        --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    # Ensure repo root is importable when invoked as a script.
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

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
    parser.add_argument(
        "--manifest",
        help="Optional manifest.yaml listing expected decisions for evaluation.",
    )
    args = parser.parse_args(argv)

    from trpc_agent_sdk.safety import PolicyConfig
    from trpc_agent_sdk.safety import SafetyScanner
    from trpc_agent_sdk.safety import ScanInput
    from trpc_agent_sdk.safety import AuditLogger

    policy_path = Path(args.policy)
    if not policy_path.is_file():
        print(f"error: policy file not found: {policy_path}", file=sys.stderr)
        return 1

    policy = PolicyConfig.from_yaml(policy_path)
    scanner = SafetyScanner(policy=policy)
    audit = AuditLogger(args.audit)

    targets: list[Path] = []
    if args.script:
        targets.append(Path(args.script))
    if args.samples:
        samples_dir = Path(args.samples)
        targets.extend(sorted(p for p in samples_dir.iterdir() if p.is_file()))
    if not targets:
        parser.error("provide --script or --samples")

    all_reports: list[dict] = []
    any_denied = False
    review = 0

    for target in targets:
        if not target.is_file():
            continue
        if target.suffix in {".yaml", ".yml", ".json", ".md", ".jsonl"}:
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
        if report.decision.value == "needs_human_review":
            review += 1
        if args.verbose or report.decision.value != "allow":
            print(
                f"[{report.decision.value.upper():>20}] {target.name} "
                f"(risk={report.risk_level.value}, rules={report.rule_ids})"
            )
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

    allowed = sum(1 for r in all_reports if r["decision"] == "allow")
    denied = sum(1 for r in all_reports if r["decision"] == "deny")
    review_count = sum(1 for r in all_reports if r["decision"] == "needs_human_review")
    print(
        f"\nSummary: {len(all_reports)} scanned | "
        f"{allowed} allow | {denied} deny | {review_count} needs_review"
    )

    if args.manifest:
        _check_manifest(Path(args.manifest), all_reports)

    if any_denied:
        return 1
    if review_count > 0:
        return 2
    return 0


def _check_manifest(manifest_path: Path, reports: list[dict]) -> None:
    import yaml

    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    cases = data.get("cases") or data.get("samples") or data
    if not isinstance(cases, list):
        print("manifest: unexpected format", file=sys.stderr)
        return
    by_name = {Path(r["script_path"]).name: r for r in reports}
    ok = 0
    fail = 0
    for case in cases:
        name = case.get("file") or case.get("name")
        expect = case.get("expect") or case.get("decision")
        if not name or not expect:
            continue
        rec = by_name.get(name)
        if rec is None:
            print(f"manifest MISS {name}")
            fail += 1
            continue
        if rec["decision"] == expect:
            ok += 1
        else:
            print(f"manifest FAIL {name}: got {rec['decision']}, expect {expect}")
            fail += 1
    print(f"Manifest: {ok} ok | {fail} fail")


def _infer_language(path: Path) -> str:
    if path.suffix == ".py":
        return "python"
    if path.suffix in (".sh", ".bash"):
        return "bash"
    return ""


if __name__ == "__main__":
    sys.exit(main())
