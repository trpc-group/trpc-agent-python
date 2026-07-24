# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for the code review dry-run diff parser."""

from __future__ import annotations

from examples.code_review_agent.agent.diff_parser import parse_unified_diff
from examples.code_review_agent.agent.schemas import ChangedLineKind


def test_parse_empty_diff() -> None:
    parsed = parse_unified_diff("")

    assert parsed.files == []


def test_parse_modified_file_line_numbers() -> None:
    diff = """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 def handler(request):
+    status = "ok"
     return {"ok": True}
"""

    parsed = parse_unified_diff(diff)

    assert len(parsed.files) == 1
    diff_file = parsed.files[0]
    assert diff_file.old_path == "src/app.py"
    assert diff_file.new_path == "src/app.py"
    assert diff_file.status == "modified"
    assert len(diff_file.hunks) == 1
    lines = diff_file.hunks[0].changed_lines
    assert lines[0].kind == ChangedLineKind.CONTEXT
    assert lines[0].old_line_number == 1
    assert lines[0].new_line_number == 1
    assert lines[1].kind == ChangedLineKind.ADDED
    assert lines[1].old_line_number is None
    assert lines[1].new_line_number == 2
    assert lines[2].kind == ChangedLineKind.CONTEXT
    assert lines[2].old_line_number == 2
    assert lines[2].new_line_number == 3


def test_removed_line_has_no_new_line_number() -> None:
    diff = """diff --git a/src/config.py b/src/config.py
--- a/src/config.py
+++ b/src/config.py
@@ -1,3 +1,2 @@
 DEBUG = False
-API_KEY = "FAKE_REMOVED_SECRET_VALUE_1234567890"
 TIMEOUT = 30
"""

    parsed = parse_unified_diff(diff)

    removed = parsed.files[0].hunks[0].changed_lines[1]
    assert removed.kind == ChangedLineKind.REMOVED
    assert removed.old_line_number == 2
    assert removed.new_line_number is None


def test_parse_binary_diff() -> None:
    diff = """diff --git a/assets/logo.png b/assets/logo.png
index 1111111..2222222 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""

    parsed = parse_unified_diff(diff)

    assert parsed.files[0].is_binary is True
    assert parsed.files[0].status == "binary"


def test_parse_renamed_file_paths() -> None:
    diff = """diff --git a/src/old_config.py b/src/new_config.py
similarity index 90%
rename from src/old_config.py
rename to src/new_config.py
--- a/src/old_config.py
+++ b/src/new_config.py
@@ -1 +1,2 @@
 DEBUG = False
+TOKEN = "FAKE_GITHUB_TOKEN_VALUE_1234567890"
"""

    parsed = parse_unified_diff(diff)

    assert parsed.files[0].status == "renamed"
    assert parsed.files[0].old_path == "src/old_config.py"
    assert parsed.files[0].new_path == "src/new_config.py"
    assert parsed.files[0].hunks[0].changed_lines[1].new_line_number == 2
