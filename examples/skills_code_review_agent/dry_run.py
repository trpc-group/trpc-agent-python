#!/usr/bin/env python3
"""Dry-run mode for the code review agent.

Runs the full review pipeline without real LLM calls or sandbox execution.
Uses pattern-based detection only, which is fast enough to complete the
full 8-fixture test suite in under 2 minutes (Issue #92 AC-07).

Usage:
    python dry_run.py --fixture 01_clean
    python dry_run.py --fixture 01_clean --output-dir /tmp/review --db-path /tmp/review.db
    python dry_run.py --all  # Run all 8 fixtures
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

# Ensure the package is importable
_parent = Path(__file__).resolve().parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from config import ReviewAgentConfig
from review_agent import run_review, mask_secrets
from storage.models import ReviewResult, TaskStatus


FIXTURE_NAMES = [
    "01_clean",
    "02_security_leak",
    "03_async_resource_leak",
    "04_db_connection_leak",
    "05_test_missing",
    "06_duplicate_finding",
    "07_sandbox_failure",
    "08_secret_masking",
]

FIXTURE_DIR = Path(__file__).parent / "evals" / "fixtures"


def run_single_fixture(
    fixture_name: str,
    output_dir: str,
    db_path: str,
    verbose: bool = False,
) -> Optional[ReviewResult]:
    """Run the review pipeline on a single fixture.

    Args:
        fixture_name: Name of the fixture (e.g. "01_clean").
        output_dir: Directory for output reports.
        db_path: Path to the SQLite database.
        verbose: If True, print progress messages.

    Returns:
        ReviewResult or None on failure.
    """
    os.makedirs(output_dir, exist_ok=True)

    config = ReviewAgentConfig(
        input_source="fixture",
        input_value=fixture_name,
        output_dir=output_dir,
        sandbox_type="local",
        dry_run=True,
        fake_model=True,
        db_path=db_path,
    )

    if verbose:
        print(f"  Running dry-review on '{fixture_name}'...", end=" ")

    start = time.time()
    result = run_review(config)
    elapsed = (time.time() - start) * 1000

    if verbose:
        status = "✅" if result and result.task.status == TaskStatus.COMPLETED else "❌"
        n_findings = len(result.findings) + len(result.warnings) + len(result.needs_human_review) if result else 0
        print(f" {status} {n_findings} findings, {elapsed:.0f}ms")

    return result


def run_all_fixtures(output_base: str, db_path: str, verbose: bool = True) -> dict[str, Any]:
    """Run all 8 fixtures and collect results.

    Args:
        output_base: Base directory for output (each fixture gets a subdir).
        db_path: Path to the SQLite database.
        verbose: If True, print progress messages.

    Returns:
        Dict with summary of all runs.
    """
    results: dict[str, Any] = {
        "total": len(FIXTURE_NAMES),
        "passed": 0,
        "failed": 0,
        "fixtures": {},
    }

    start = time.time()

    for fixture_name in FIXTURE_NAMES:
        fixture_output = os.path.join(output_base, fixture_name)
        fixture_db = db_path

        result = run_single_fixture(fixture_name, fixture_output, fixture_db, verbose=verbose)

        fixture_result = {
            "status": "passed" if result and result.task.status == TaskStatus.COMPLETED else "failed",
            "finding_count": len(result.findings) + len(result.warnings) + len(result.needs_human_review) if result else 0,
            "report_json": result.report_path_json if result else None,
            "report_md": result.report_path_md if result else None,
            "error": result.task.error_message if result and result.task.error_message else None,
        }

        if fixture_result["status"] == "passed":
            results["passed"] += 1
        else:
            results["failed"] += 1

        results["fixtures"][fixture_name] = fixture_result

    results["total_duration_ms"] = (time.time() - start) * 1000

    if verbose:
        print(f"\n📊 Dry-run complete: {results['passed']}/{results['total']} passed, "
              f"{results['total_duration_ms']:.0f}ms total")

    return results


def main() -> None:
    """CLI entry point for dry-run mode."""
    parser = argparse.ArgumentParser(
        description="ReviewMind Dry-run — run the review pipeline without LLM calls",
    )
    parser.add_argument(
        "--fixture", type=str, default=None,
        help="Fixture name to run (e.g. '01_clean'). Omit to run all.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Run all 8 fixtures",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="Output directory for reports (default: ./reports/<fixture>)",
    )
    parser.add_argument(
        "--db-path", type=str, default=None,
        help="Path to SQLite database (default: <output_dir>/review.db)",
    )

    args = parser.parse_args()

    if args.all:
        output_base = args.output_dir or os.path.join(os.getcwd(), "reports")
        db_path = args.db_path or os.path.join(output_base, "review.db")
        results = run_all_fixtures(output_base, db_path, verbose=True)
        print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(0 if results["failed"] == 0 else 1)

    if args.fixture:
        fixture_name = args.fixture
        if fixture_name not in FIXTURE_NAMES:
            print(f"Unknown fixture: {fixture_name}")
            print(f"Available: {', '.join(FIXTURE_NAMES)}")
            sys.exit(1)

        output_dir = args.output_dir or os.path.join(os.getcwd(), "reports", fixture_name)
        db_path = args.db_path or os.path.join(output_dir, "review.db")

        result = run_single_fixture(fixture_name, output_dir, db_path, verbose=True)
        if result and result.task.status == TaskStatus.COMPLETED:
            print(f"\n✅ Review complete: {len(result.findings)} critical, "
                  f"{len(result.warnings)} warnings, "
                  f"{len(result.needs_human_review)} needs review")
            print(f"   JSON report: {result.report_path_json}")
            print(f"   MD report:  {result.report_path_md}")
            sys.exit(0)
        else:
            error = result.task.error_message if result else "Unknown error"
            print(f"\n❌ Review failed: {error}")
            sys.exit(1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()