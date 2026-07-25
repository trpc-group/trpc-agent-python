# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under the Apache License Version 2.0.
"""End-to-end test for the public tool safety scanner CLI."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

REPOSITORY = Path(__file__).parents[3]
EXAMPLE_DIR = REPOSITORY / "examples" / "tool_safety_guard"


def test_cli_scans_exactly_twelve_samples(tmp_path: Path):
    """The acceptance command ignores cache directories and writes both artifacts."""
    report_path = tmp_path / "report.json"
    audit_path = tmp_path / "audit.jsonl"

    result = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE_DIR / "tool_safety_check.py"),
            "--report",
            str(report_path),
            "--audit",
            str(audit_path),
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    audit_events = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert report["sample_count"] == 12
    assert report["allow"] == 2
    assert report["deny"] == 7
    assert report["needs_human_review"] == 3
    assert len(audit_events) == 12
    assert all("script" not in event for event in audit_events)


def test_cli_default_run_does_not_rewrite_committed_artifacts():
    """A no-output scan must not churn the committed example snapshots."""
    artifacts = [
        EXAMPLE_DIR / "tool_safety_report.json",
        EXAMPLE_DIR / "tool_safety_audit.jsonl",
    ]
    before = {path: path.read_bytes() for path in artifacts}

    result = subprocess.run(
        [
            sys.executable,
            str(EXAMPLE_DIR / "tool_safety_check.py"),
        ],
        cwd=REPOSITORY,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "Summary: 12 samples" in result.stdout
    assert {path: path.read_bytes() for path in artifacts} == before
