# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for code review dry-run report rendering."""

from __future__ import annotations

from examples.code_review_agent.agent.pipeline import run_dry_review
from examples.code_review_agent.agent.report import render_markdown_report
from examples.code_review_agent.agent.report import report_to_json


def test_clean_report_says_no_findings() -> None:
    diff = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,2 @@
 def handler(request):
+    status = "ok"
"""

    report = run_dry_review(diff)
    markdown = render_markdown_report(report)

    assert report.findings == []
    assert "No high-confidence findings" in markdown


def test_secret_report_json_and_markdown_are_redacted() -> None:
    diff = """diff --git a/src/config.py b/src/config.py
--- a/src/config.py
+++ b/src/config.py
@@ -1 +1,2 @@
 DEBUG = False
+API_KEY = "FAKE_TEST_SECRET_VALUE_1234567890"
"""

    report = run_dry_review(diff)
    raw_json = report_to_json(report)
    markdown = render_markdown_report(report)

    assert "FAKE_TEST_SECRET_VALUE_1234567890" not in raw_json
    assert "FAKE_TEST_SECRET_VALUE_1234567890" not in markdown
    assert "<REDACTED_SECRET>" in raw_json
    assert "Hard-coded secret" in markdown
    assert report.metrics.finding_count == 1
    assert report.metrics.severity_counts == {"high": 1}
