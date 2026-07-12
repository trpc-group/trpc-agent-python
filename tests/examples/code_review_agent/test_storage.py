# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for code review SQLite storage."""

from __future__ import annotations

from pathlib import Path

from examples.code_review_agent.agent.diff_parser import parse_unified_diff
from examples.code_review_agent.agent.inputs import build_review_input
from examples.code_review_agent.agent.pipeline import ReviewRunConfig
from examples.code_review_agent.agent.pipeline import run_review
from examples.code_review_agent.agent.storage import ReviewStorage


def test_run_review_persists_queryable_task(tmp_path: Path) -> None:
    diff = """diff --git a/src/config.py b/src/config.py
--- a/src/config.py
+++ b/src/config.py
@@ -1 +1,2 @@
 DEBUG = False
+API_KEY = "FAKE_TEST_SECRET_VALUE_1234567890"
"""
    parsed = parse_unified_diff(diff)
    review_input = build_review_input(parsed, diff_text=diff, input_type="diff_file", diff_file="fixture.diff")
    db_path = tmp_path / "reviews.sqlite"

    report = run_review(diff, parsed_diff=parsed, review_input=review_input, config=ReviewRunConfig(db_path=db_path))

    storage = ReviewStorage(db_path)
    try:
        task = storage.get_task(report.task_id or "")
    finally:
        storage.close()

    assert task is not None
    assert task["task"]["status"] in {"completed", "completed_with_warnings"}
    assert task["sandbox_runs"]
    assert task["filter_decisions"]
    assert task["findings"]
    assert task["report"] is not None
    assert "FAKE_TEST_SECRET_VALUE_1234567890" not in str(task)
