#!/usr/bin/env python3
"""Hidden sample evaluation script for ReviewMind.

Runs the review pipeline against hidden test samples and computes
detection rate (AC-02) and false positive rate (AC-03).

Usage:
    python evals/run_hidden_eval.py
    python evals/run_hidden_eval.py --verbose
"""

from __future__ import annotations

import json
import os
import sys
import time
from enum import Enum
from pathlib import Path
from typing import Any

# Ensure the package is importable
_parent = Path(__file__).resolve().parent.parent
if str(_parent) not in sys.path:
    sys.path.insert(0, str(_parent))

from config import ReviewAgentConfig
from review_agent import run_review, parse_diff
from storage.models import ReviewResult, TaskStatus, FindingSeverity

HIDDEN_DIR = Path(__file__).parent / "hidden_fixtures"

# Ground truth: expected findings for each hidden sample
# Format: {fixture_name: [(file_keyword, line, severity, category_keyword, title_keyword)]}
GROUND_TRUTH: dict[str, list[tuple[str, int, str, str, str]]] = {
    "hidden_01_sql_injection": [
        ("user_dao.py", 14, "critical", "security", "SQL注入"),
        ("user_dao.py", 14, "critical", "db", "SQL注入"),  # DB-layer duplicate (different category)
        ("user_dao.py", 12, "warning", "db", "连接未关闭"),
    ],
    "hidden_02_aws_secret": [
        ("aws_config.py", 3, "critical", "secret", "AWS Access Key"),
    ],
    "hidden_03_clean": [],
    "hidden_04_cmd_injection": [
        ("deploy.py", 11, "critical", "security", "命令注入"),
    ],
    "hidden_05_file_leak": [
        ("file_processor.py", 5, "warning", "resource_leak", "文件句柄"),
        ("file_processor.py", 12, "warning", "resource_leak", "文件句柄"),
    ],
    "hidden_06_jwt_leak": [
        ("auth_config.py", 3, "critical", "secret", "JWT Token"),
        ("auth_config.py", 4, "critical", "secret", "私钥"),
    ],
    "hidden_07_async_leak": [
        ("async_worker.py", 10, "warning", "resource_leak", "aiohttp"),
        ("async_worker.py", 14, "warning", "async", "阻塞调用"),
    ],
    "hidden_08_db_url": [
        ("db_config.py", 3, "critical", "secret", "数据库连接字符串"),
    ],
}


def match_finding(actual: dict, expected: tuple) -> bool:
    """Check if an actual finding matches an expected ground truth entry."""
    file_kw, line, severity, category, title_kw = expected
    # File match: expected keyword in actual file path
    if file_kw not in actual.get("file_path", ""):
        return False
    # Line match: exact or within 1 line
    actual_line = actual.get("line_number", 0)
    if actual_line not in (line, line - 1, line + 1):
        return False
    # Severity match: compare enum values (or string values)
    actual_severity = str(actual.get("severity", ""))
    if isinstance(actual.get("severity"), Enum):
        actual_severity = actual["severity"].value
    if actual_severity != severity:
        return False
    # Category match: compare enum values (or string values)
    actual_category = str(actual.get("category", ""))
    if isinstance(actual.get("category"), Enum):
        actual_category = actual["category"].value
    if category not in actual_category:
        return False
    # Title match: expected keyword in actual title
    if title_kw not in actual.get("title", ""):
        return False
    return True


def run_evaluation(verbose: bool = False) -> dict[str, Any]:
    """Run evaluation on all hidden samples.

    Args:
        verbose: If True, print detailed per-sample results.

    Returns:
        Dict with evaluation results: detection_rate, false_positive_rate,
        per_sample details.
    """
    results: dict[str, Any] = {
        "total_samples": len(GROUND_TRUTH),
        "total_expected_findings": 0,
        "total_detected": 0,
        "total_false_positives": 0,
        "per_sample": {},
    }

    for fixture_name, expected_findings in GROUND_TRUTH.items():
        output_dir = f"/tmp/review_hidden/{fixture_name}"
        os.makedirs(output_dir, exist_ok=True)

        fixture_path = HIDDEN_DIR / f"{fixture_name}.diff"
        # Some fixtures (e.g. hidden_08_db_url) embed fake credentials and are
        # generated dynamically at runtime instead of being committed as static
        # files, to avoid CodeCC secret-detection false positives. Materialize
        # such dynamic fixtures to a temp file so the review pipeline can read it.
        if not fixture_path.exists():
            try:
                from evals.fixtures.generate_fixtures import get_fixture_content
                generated = get_fixture_content(fixture_name)
            except ImportError:
                generated = None
            if generated is not None:
                fixture_path = Path(output_dir) / f"{fixture_name}.diff"
                with open(fixture_path, "w", encoding="utf-8") as f:
                    f.write(generated)
        if not fixture_path.exists():
            if verbose:
                print(f"  ⚠️  Fixture not found: {fixture_name}")
            results["per_sample"][fixture_name] = {"error": "fixture not found"}
            continue

        if verbose:
            print(f"  🔍 {fixture_name}...", end=" ")

        # Run the pipeline
        db_path = f"{output_dir}/review.db"

        config = ReviewAgentConfig(
            input_source="diff_file",
            input_value=str(fixture_path),
            output_dir=output_dir,
            sandbox_type="local",
            dry_run=True,
            fake_model=True,
            db_path=db_path,
        )

        start = time.time()
        result = run_review(config)
        elapsed = (time.time() - start) * 1000

        sample_result: dict[str, Any] = {
            "expected_count": len(expected_findings),
            "elapsed_ms": elapsed,
            "status": "failed" if not result or result.task.status == TaskStatus.FAILED else "completed",
        }

        if result and result.task.status == TaskStatus.COMPLETED:
            all_findings = (
                [f.model_dump() for f in result.findings] +
                [f.model_dump() for f in result.warnings] +
                [f.model_dump() for f in result.needs_human_review]
            )

            # Compute detection rate
            detected = 0
            for expected in expected_findings:
                for actual in all_findings:
                    if match_finding(actual, expected):
                        detected += 1
                        break

            # Compute false positives (findings that don't match any expected)
            false_positives = 0
            matched_expected = set()
            for actual in all_findings:
                is_fp = True
                for i, expected in enumerate(expected_findings):
                    if match_finding(actual, expected):
                        is_fp = False
                        matched_expected.add(i)
                        break
                if is_fp:
                    false_positives += 1

            sample_result["detected"] = detected
            sample_result["false_positives"] = false_positives
            sample_result["total_findings"] = len(all_findings)
            sample_result["findings"] = [
                {"title": f["title"], "file": f["file_path"], "line": f["line_number"],
                 "severity": f["severity"], "category": f["category"]}
                for f in all_findings
            ]

            results["total_expected_findings"] += len(expected_findings)
            results["total_detected"] += detected
            results["total_false_positives"] += false_positives

            if verbose:
                detection_pct = (detected / len(expected_findings) * 100) if expected_findings else 100
                print(f" {detected}/{len(expected_findings)} detected, {false_positives} FP ({elapsed:.0f}ms)")
        else:
            sample_result["error"] = result.task.error_message if result else "pipeline returned None"
            if verbose:
                print(f" ❌ {sample_result.get('error', 'unknown')}")

        results["per_sample"][fixture_name] = sample_result

    # Compute overall rates
    total_expected = results["total_expected_findings"]
    total_detected = results["total_detected"]
    total_fp = results["total_false_positives"]

    results["detection_rate"] = (total_detected / total_expected * 100) if total_expected else 100.0
    results["false_positive_rate"] = (total_fp / total_expected * 100) if total_expected else 0.0
    results["pass_detection"] = results["detection_rate"] >= 80.0
    results["pass_fp"] = results["false_positive_rate"] <= 15.0

    return results


def main() -> None:
    """CLI entry point."""
    verbose = "-v" in sys.argv or "--verbose" in sys.argv

    print("=" * 60)
    print("  ReviewMind Hidden Sample Evaluation")
    print("  AC-02: Detection Rate ≥ 80%")
    print("  AC-03: False Positive Rate ≤ 15%")
    print("=" * 60)
    print()

    results = run_evaluation(verbose=verbose)

    print()
    print("-" * 60)
    print(f"  Total samples:        {results['total_samples']}")
    print(f"  Total expected findings: {results['total_expected_findings']}")
    print(f"  Detected:             {results['total_detected']}")
    print(f"  False positives:      {results['total_false_positives']}")
    print(f"  Detection rate:       {results['detection_rate']:.1f}%  {'✅ PASS' if results['pass_detection'] else '❌ FAIL'} (≥ 80%)")
    print(f"  False positive rate:  {results['false_positive_rate']:.1f}%  {'✅ PASS' if results['pass_fp'] else '❌ FAIL'} (≤ 15%)")
    print("-" * 60)

    if verbose:
        print()
        for name, sample in results["per_sample"].items():
            status = "✅" if sample.get("status") == "completed" else "❌"
            print(f"  {status} {name}")
            if "findings" in sample:
                for f in sample["findings"]:
                    print(f"      [{f['severity']}] {f['title']} — {f['file']}:L{f['line']}")
            if "error" in sample:
                print(f"      Error: {sample['error']}")

    sys.exit(0 if results["pass_detection"] and results["pass_fp"] else 1)


if __name__ == "__main__":
    main()