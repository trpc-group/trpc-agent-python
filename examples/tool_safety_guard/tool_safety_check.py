#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""Scan one script or all public safety guard samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

EXAMPLE_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = EXAMPLE_DIR.parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from trpc_agent_sdk.tools.safety import Decision  # noqa: E402
from trpc_agent_sdk.tools.safety import JsonlAuditSink  # noqa: E402
from trpc_agent_sdk.tools.safety import SafetyScanRequest  # noqa: E402
from trpc_agent_sdk.tools.safety import ToolSafetyPolicy  # noqa: E402
from trpc_agent_sdk.tools.safety import ToolSafetyScanner  # noqa: E402


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Scan Python or Bash before Tool execution")
    parser.add_argument("script", nargs="?", type=Path, help="script to scan; omit to scan samples/")
    parser.add_argument("--language", choices=("python", "bash"), help="override language detection")
    parser.add_argument("--policy", type=Path, default=EXAMPLE_DIR / "tool_safety_policy.yaml")
    parser.add_argument("--report", type=Path, help="optional JSON report output path")
    parser.add_argument("--audit", type=Path, help="optional JSONL audit output path")
    return parser.parse_args()


def language_for(path: Path, override: str | None) -> str:
    """Infer a supported language from the file suffix."""
    if override:
        return override
    return "bash" if path.suffix in {".sh", ".bash"} else "python"


def main() -> int:
    """Run scans, write structured artifacts, and print an acceptance summary."""
    args = parse_args()
    policy = ToolSafetyPolicy.from_yaml(args.policy)
    scanner = ToolSafetyScanner(policy)
    audit = JsonlAuditSink(args.audit) if args.audit else None
    paths = ([args.script] if args.script else sorted(path for path in (EXAMPLE_DIR / "samples").iterdir()
                                                      if path.is_file() and path.suffix in {".py", ".sh", ".bash"}))
    reports = []
    if args.audit and args.audit.exists():
        args.audit.unlink()

    for path in paths:
        report = scanner.scan(
            SafetyScanRequest(
                script=path.read_text(encoding="utf-8"),
                language=language_for(path, args.language),
                tool_name="tool_safety_demo",
                tool_metadata={"sample": path.name},
            ))
        blocked = report.decision != Decision.ALLOW
        if audit:
            audit.emit(report.to_audit_event(blocked=blocked))
        payload = report.model_dump(mode="json")
        payload["sample"] = path.name
        reports.append(payload)
        rules = ",".join(report.rule_ids) or "-"
        print(f"{path.name:<28} {report.decision.value:<22} "
              f"{report.risk_level.value:<8} {report.duration_ms:>7.3f} ms  {rules}")

    summary = {
        "sample_count": len(reports),
        "allow": sum(report["decision"] == "allow" for report in reports),
        "deny": sum(report["decision"] == "deny" for report in reports),
        "needs_human_review": sum(report["decision"] == "needs_human_review" for report in reports),
        "max_duration_ms": max((report["duration_ms"] for report in reports), default=0),
        "reports": reports,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(summary, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    print("\nSummary: "
          f"{summary['sample_count']} samples, {summary['allow']} allow, "
          f"{summary['deny']} deny, {summary['needs_human_review']} review, "
          f"max {summary['max_duration_ms']:.3f} ms")
    if args.report:
        print(f"Report: {args.report}")
    if args.audit:
        print(f"Audit:  {args.audit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
