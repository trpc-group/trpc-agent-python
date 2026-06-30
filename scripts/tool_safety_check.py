# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI for the Tool Script Safety Guard.

Scan a single script or a directory of scripts, emit a structured JSON report
and a JSONL audit log, and exit non-zero when anything is denied (useful in CI).

Example::

    python scripts/tool_safety_check.py examples/tool_safety_guard/samples \\
        --policy examples/tool_safety_guard/tool_safety_policy.yaml \\
        --report tool_safety_report.json \\
        --audit tool_safety_audit.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from trpc_agent_sdk.tools.safety import AuditLogger
from trpc_agent_sdk.tools.safety import Decision
from trpc_agent_sdk.tools.safety import Language
from trpc_agent_sdk.tools.safety import SafetyEngine
from trpc_agent_sdk.tools.safety import ScanInput
from trpc_agent_sdk.tools.safety import load_policy

_SUFFIX_LANG = {
    ".py": Language.PYTHON,
    ".sh": Language.BASH,
    ".bash": Language.BASH,
}


def detect_language(path: Path) -> Language:
    return _SUFFIX_LANG.get(path.suffix.lower(), Language.UNKNOWN)


def collect_files(target: Path) -> list[Path]:
    """Return the scripts to scan, sorted for stable output."""
    if target.is_file():
        return [target]
    files: list[Path] = []
    for suffix in _SUFFIX_LANG:
        files.extend(target.rglob(f"*{suffix}"))
    return sorted(set(files))


def scan_path(target: Path, policy_path: Optional[str], audit_path: Optional[str]) -> dict:
    engine = SafetyEngine(load_policy(policy_path))
    audit = AuditLogger(audit_path)
    base = target if target.is_dir() else target.parent

    reports: list[dict] = []
    counts = {Decision.ALLOW: 0, Decision.DENY: 0, Decision.NEEDS_HUMAN_REVIEW: 0}
    for file_path in collect_files(target):
        try:
            script = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError as ex:
            print(f"[skip] cannot read {file_path}: {ex}", file=sys.stderr)
            continue
        rel = str(file_path.relative_to(base)) if file_path != base else file_path.name
        report = engine.scan(ScanInput(script=script, tool_name=rel, language=detect_language(file_path)))
        counts[report.decision] = counts.get(report.decision, 0) + 1
        audit.log(report, blocked=report.decision == Decision.DENY)
        record = report.to_dict()
        record["file"] = str(file_path)
        reports.append(record)

    return {
        "policy": policy_path or "default",
        "summary": {
            "total": len(reports),
            "allow": counts[Decision.ALLOW],
            "deny": counts[Decision.DENY],
            "needs_human_review": counts[Decision.NEEDS_HUMAN_REVIEW],
        },
        "reports": reports,
    }


def print_summary(result: dict) -> None:
    print(f"Scanned {result['summary']['total']} file(s) "
          f"using policy: {result['policy']}")
    for r in result["reports"]:
        print(f"  [{r['decision']:>18}] {r['risk_level']:>8}  {r['file']}")
    s = result["summary"]
    print(f"Summary: allow={s['allow']} deny={s['deny']} "
          f"needs_human_review={s['needs_human_review']}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tool Script Safety Guard scanner")
    parser.add_argument("target", help="Script file or directory to scan")
    parser.add_argument("--policy", help="Path to tool_safety_policy.yaml (default: built-in)")
    parser.add_argument("--report", help="Write the structured JSON report to this path")
    parser.add_argument("--audit", help="Write the JSONL audit log to this path")
    parser.add_argument("--fail-on", choices=["deny", "review", "never"], default="deny",
                        help="Exit non-zero when a decision at this level or worse is found")
    args = parser.parse_args(argv)

    target = Path(args.target)
    if not target.exists():
        print(f"error: target not found: {target}", file=sys.stderr)
        return 2

    result = scan_path(target, args.policy, args.audit)

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print_summary(result)

    s = result["summary"]
    if args.fail_on == "deny" and s["deny"] > 0:
        return 1
    if args.fail_on == "review" and (s["deny"] > 0 or s["needs_human_review"] > 0):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
