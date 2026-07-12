# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for deterministic fake reviewer."""

from __future__ import annotations

from examples.code_review_agent.agent.diff_parser import parse_unified_diff
from examples.code_review_agent.agent.fake_reviewer import review_with_fake_model
from examples.code_review_agent.agent.schemas import FindingSource
from examples.code_review_agent.agent.schemas import Severity


def test_clean_diff_returns_no_findings() -> None:
    diff = """diff --git a/src/app.py b/src/app.py
--- a/src/app.py
+++ b/src/app.py
@@ -1 +1,2 @@
 def handler(request):
+    status = "ok"
"""

    findings = review_with_fake_model(parse_unified_diff(diff))

    assert findings == []


def test_added_secret_returns_high_severity_finding() -> None:
    diff = """diff --git a/src/config.py b/src/config.py
--- a/src/config.py
+++ b/src/config.py
@@ -1 +1,2 @@
 DEBUG = False
+API_KEY = "FAKE_TEST_SECRET_VALUE_1234567890"
"""

    findings = review_with_fake_model(parse_unified_diff(diff))

    assert len(findings) == 1
    assert findings[0].severity == Severity.HIGH
    assert findings[0].category == "secrets"
    assert findings[0].file == "src/config.py"
    assert findings[0].line == 2
    assert findings[0].source == FindingSource.FAKE_MODEL


def test_removed_only_secret_is_ignored() -> None:
    diff = """diff --git a/src/config.py b/src/config.py
--- a/src/config.py
+++ b/src/config.py
@@ -1,2 +1 @@
 DEBUG = False
-API_KEY = "FAKE_REMOVED_SECRET_VALUE_1234567890"
"""

    findings = review_with_fake_model(parse_unified_diff(diff))

    assert findings == []
