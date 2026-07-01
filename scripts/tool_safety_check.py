#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Standalone Tool Safety scanner for scripts.

Usage:
    python scripts/tool_safety_check.py example.py
    python scripts/tool_safety_check.py example.sh --policy tool_safety_policy.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from trpc_agent_sdk._tool_safety import SafetyReview
from trpc_agent_sdk._tool_safety import SafetyReviewer

_EXIT_CODES = {
    "allow": 0,
    "deny": 1,
    "needs_human_review": 2,
}


def main(argv: list[str] | None = None) -> int:
    """Run the Tool Safety scanner CLI."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    target = Path(args.path)
    try:
        source = target.read_text(encoding="utf-8")
    except OSError as exc:
        parser.error(f"unable to read {target}: {exc}")

    policy_path = Path(args.policy) if args.policy else None
    if policy_path is not None and not policy_path.exists():
        parser.error(f"policy file not found: {policy_path}")

    reviewer = SafetyReviewer(policy_path=policy_path)
    review = reviewer.review(
        source,
        action_type=_infer_action_type(target, source),
        tool_name="tool_safety_check",
    )
    report = _build_report(review, target)

    output_text = _format_report(report, args.format)
    print(output_text)

    if args.output:
        _write_json_report(Path(args.output), report)

    return _EXIT_CODES.get(review.decision, 1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan a Python or Bash script with Tool Safety rules.")
    parser.add_argument("path", help="Python or Bash script to scan")
    parser.add_argument("--policy", help="YAML Tool Safety policy file")
    parser.add_argument(
        "--format",
        choices=("json", "text"),
        default="json",
        help="stdout report format (default: json)",
    )
    parser.add_argument("--output", help="Write the JSON report to this file")
    return parser


def _infer_action_type(path: Path, source: str) -> str:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return "python"
    if suffix in {".sh", ".bash", ".zsh"}:
        return "bash"
    first_line = source.splitlines()[0] if source.splitlines() else ""
    if "python" in first_line:
        return "python"
    if any(shell in first_line for shell in ("bash", "sh", "zsh")):
        return "bash"
    return "python"


def _build_report(review: SafetyReview, path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "decision": review.decision,
        "risk_level": review.report.get("risk_level", ""),
        "rule_id": review.rule_id,
        "evidence": review.report.get("evidence", ""),
        "recommendation": review.report.get("recommendation", ""),
        "finding": review.finding,
    }


def _format_report(report: dict[str, Any], output_format: str) -> str:
    if output_format == "json":
        return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    return "\n".join(f"{key}: {value}" for key, value in report.items())


def _write_json_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
