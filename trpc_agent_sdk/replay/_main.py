# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Command-line entry point for the replay harness."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ._backends import resolve_backend_names
from ._cases import DEFAULT_CASES_PATH
from ._diff import build_diff_report
from ._harness import run_replay_harness
from ._harness import write_diff_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output", type=Path, default=Path("session_memory_summary_diff_report.json"))
    parser.add_argument("--work-dir", type=Path, default=Path(".replay-work"))
    parser.add_argument("--backends", help="Comma-separated override: inmemory,sqlite,sql,redis")
    args = parser.parse_args(argv)
    backend_names = None
    if args.backends:
        backend_names = [name.strip() for name in args.backends.split(",") if name.strip()]
    else:
        backend_names = resolve_backend_names()

    run = asyncio.run(run_replay_harness(
        work_dir=args.work_dir,
        cases_path=args.cases,
        backend_names=backend_names,
    ))
    report = build_diff_report(run)
    write_diff_report(report, args.output)
    _print_report_summary(report, args.output)
    summary = report["summary"]
    return 1 if summary["unexpected_diff_count"] or summary["invariant_failure_count"] else 0


def _print_report_summary(report, output_path: Path) -> None:
    print("Session / Memory / Summary Replay Consistency")
    print(f"Mode: {report['mode']}")
    print(f"Backends: {', '.join(report['backends'])}")
    print("")
    print(f"{'CASE':32} {'STATUS':8} {'DIFFS':>5} {'ALLOWED':>7}")
    print("-" * 58)
    for case in report["cases"]:
        print(
            f"{case['case_id'][:32]:32} {case['status'].upper():8} "
            f"{len(case['differences']):5d} {len(case['allowed_diffs']):7d}")
    print("-" * 58)
    summary = report["summary"]
    print(f"Passed: {summary['passed_case_count']}/{summary['case_count']} cases")
    print(f"Unexpected diffs: {summary['unexpected_diff_count']}")
    print(f"Invariant failures: {summary['invariant_failure_count']}")
    print(f"Elapsed: {summary['elapsed_seconds']:.3f}s")
    print(f"Report: {output_path}")


if __name__ == "__main__":
    sys.exit(main())