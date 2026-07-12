# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""CLI tests for the code review dry-run example."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RUN_REVIEW = ROOT / "examples" / "code_review_agent" / "run_review.py"
FIXTURES = ROOT / "examples" / "code_review_agent" / "fixtures"


def test_cli_json_clean_diff_exits_zero() -> None:
    result = subprocess.run(
        [sys.executable, str(RUN_REVIEW), "--diff-file", str(FIXTURES / "clean.diff"), "--json"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["findings"] == []
    assert payload["metrics"]["file_count"] == 1


def test_cli_markdown_secret_diff_is_redacted() -> None:
    result = subprocess.run(
        [sys.executable, str(RUN_REVIEW), "--diff-file", str(FIXTURES / "hardcoded_secret.diff"), "--markdown"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "Hard-coded secret" in result.stdout
    assert "FAKE_TEST_SECRET_VALUE_1234567890" not in result.stdout


def test_cli_output_dir_writes_reports(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUN_REVIEW),
            "--diff-file",
            str(FIXTURES / "hardcoded_secret.diff"),
            "--output-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert (tmp_path / "review_report.json").is_file()
    assert (tmp_path / "review_report.md").is_file()
    assert "FAKE_TEST_SECRET_VALUE_1234567890" not in (tmp_path / "review_report.json").read_text(encoding="utf-8")


def test_cli_fail_on_findings_exits_one_for_secret_diff() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(RUN_REVIEW),
            "--diff-file",
            str(FIXTURES / "hardcoded_secret.diff"),
            "--fail-on-findings",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
