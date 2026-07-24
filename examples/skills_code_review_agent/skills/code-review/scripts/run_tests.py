#!/usr/bin/env python3
"""Run unit tests and output results.

Usage:
    python run_tests.py <test_path> <output_file>

Output:
    JSON file with test results (passed, failed, errors, duration)
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def run_tests(test_path: str) -> dict[str, Any]:
    """Run pytest on the given test path and return results."""
    start = time.time()

    result = subprocess.run(
        [sys.executable, "-m", "pytest", test_path, "-v", "--json-report", "--no-header"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    duration = (time.time() - start) * 1000

    # Parse output
    passed = result.returncode == 0
    output = result.stdout or ""
    errors = result.stderr or ""

    # Count tests
    test_lines = [l for l in output.splitlines() if l.startswith("tests/") or "PASSED" in l or "FAILED" in l]
    passed_count = output.count("PASSED")
    failed_count = output.count("FAILED")
    error_count = output.count("ERROR")

    return {
        "success": passed,
        "duration_ms": duration,
        "passed": passed_count,
        "failed": failed_count,
        "errors": error_count,
        "output": output[:5000],
        "error_message": errors[:500] if errors else None,
    }


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python run_tests.py <test_path> <output_file>", file=sys.stderr)
        sys.exit(1)

    test_path = sys.argv[1]
    output_file = sys.argv[2]

    results = run_tests(test_path)
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    Path(output_file).write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    status = "✅" if results["success"] else "❌"
    print(f"{status} Tests: {results['passed']} passed, {results['failed']} failed, "
          f"{results['errors']} errors ({results['duration_ms']:.0f}ms)")