"""Edge case and boundary tests for the code review pipeline.

Covers: empty diffs, single-line changes, Unicode/emoji, oversized inputs,
malformed data, and other extreme scenarios.
"""

import os
import json
import tempfile
import pytest

from pipeline.diff_parser import parse_diff, summarize_diff
from pipeline.scanners import run_scanners
from pipeline.dedup import deduplicate, confidence_tier, separate_by_tiers
from pipeline.redaction import redact, redact_finding_evidence
from pipeline.types import DiffFile, DiffHunk, Finding, FindingCategory, Severity
from pipeline.config import load_config


class TestEmptyAndMinimal:
    """Empty and minimal input handling."""

    def test_empty_diff_parses(self):
        files = parse_diff("")
        assert files == []

    def test_whitespace_only_diff(self):
        files = parse_diff("   \n  \n  ")
        assert files == []

    def test_single_line_diff(self):
        diff_text = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -0,0 +1 @@
+import os"""
        files = parse_diff(diff_text)
        assert len(files) == 1
        assert files[0].filename == "test.py"

    def test_single_character_change(self):
        diff_text = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1 +1 @@
-x
+y"""
        files = parse_diff(diff_text)
        assert len(files) == 1

    def test_empty_findings_dedup(self):
        result = deduplicate([])
        assert result == []

    def test_single_finding_tiers(self):
        f = Finding(
            severity=Severity.MEDIUM,
            category=FindingCategory.SECURITY,
            file="test.py", line=1,
            title="Test", evidence="test",
            recommendation="test", confidence=0.9,
            source="test",
        )
        tiers = separate_by_tiers([f])
        assert len(tiers["high"]) == 1
        assert len(tiers["warning"]) == 0
        assert len(tiers["needs_human_review"]) == 0


class TestUnicodeAndEncoding:
    """Unicode, emoji, and multi-language input."""

    def test_unicode_diff_parses(self):
        diff_text = """diff --git a/测试.py b/测试.py
--- a/测试.py
+++ b/测试.py
@@ -1 +1 @@
-旧代码
+新代码 🎉"""
        files = parse_diff(diff_text)
        assert len(files) == 1
        assert "测试.py" in files[0].filename

    def test_emoji_in_code_line(self):
        diff_text = """diff --git a/emoji.py b/emoji.py
--- a/emoji.py
+++ b/emoji.py
@@ -1,0 +1 @@
+print("🎉🚀✅❌⚠️")"""
        files = parse_diff(diff_text)
        assert len(files) >= 1

    def test_japanese_code(self):
        diff_text = """diff --git a/main.py b/main.py
--- a/main.py
+++ b/main.py
@@ -1,0 +1 @@
+# これはテストです (This is a test)"""
        files = parse_diff(diff_text)
        assert len(files) >= 1

    def test_korean_code(self):
        diff_text = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,0 +1 @@
+# 테스트 코드입니다"""
        files = parse_diff(diff_text)
        assert len(files) >= 1

    def test_mixed_language_diff(self):
        diff_text = """diff --git a/mixed.py b/mixed.py
--- a/mixed.py
+++ b/mixed.py
@@ -1,3 +1,3 @@
-print("Hello")
-print("你好")
+print("こんにちは")
+print("안녕하세요")"""
        files = parse_diff(diff_text)
        assert len(files) >= 1


class TestLargeInputs:
    """Large and oversized input handling."""

    def test_very_long_file_name(self):
        long_name = "a" * 500 + ".py"
        diff_text = f"""diff --git a/{long_name} b/{long_name}
--- a/{long_name}
+++ b/{long_name}
@@ -1,0 +1 @@
+print("test")"""
        files = parse_diff(diff_text)
        assert len(files) == 1

    def test_many_files_in_diff(self):
        """Diff with 20 files."""
        parts = []
        for i in range(20):
            parts.append(f"""diff --git a/file_{i}.py b/file_{i}.py
--- a/file_{i}.py
+++ b/file_{i}.py
@@ -1,0 +1 @@
+x = {i}""")
        diff_text = "\n".join(parts)
        files = parse_diff(diff_text)
        assert len(files) == 20

    def test_deep_hunk(self):
        """Diff with 50 lines in a single hunk."""
        added = "\n".join(f"+line {i}" for i in range(50))
        diff_text = f"""diff --git a/big.py b/big.py
--- a/big.py
+++ b/big.py
@@ -1,0 +1,50 @@
{added}"""
        files = parse_diff(diff_text)
        assert len(files) == 1
        assert len(files[0].hunks) == 1


class TestMalformedInputs:
    """Malformed or invalid inputs that should not crash."""

    def test_gibberish_diff_text(self):
        files = parse_diff("NOT A DIFF AT ALL\nJust random text\n")
        assert files == []  # No crash, just empty

    def test_partial_diff_header(self):
        diff_text = "diff --git a/x.py b/y.py\n+just a line\n-no header"
        files = parse_diff(diff_text)
        # Should not crash
        assert isinstance(files, list)

    def test_hunk_with_no_content(self):
        diff_text = """diff --git a/x.py b/x.py
--- a/x.py
+++ b/x.py
@@ -1,0 +1,0 @@"""
        files = parse_diff(diff_text)
        assert len(files) == 1

    def test_null_bytes_in_diff(self):
        diff_text = "diff --git a/x.py b/x.py\n\0unexpected null\0\n"
        files = parse_diff(diff_text)
        assert isinstance(files, list)

    def test_really_long_line(self):
        long_line = "+" + "x" * 10000
        diff_text = f"""diff --git a/long.py b/long.py
--- a/long.py
+++ b/long.py
@@ -1,0 +1 @@
{long_line}"""
        files = parse_diff(diff_text)
        assert len(files) == 1


class TestConfidenceEdgeCases:
    """Confidence tier edge cases."""

    def test_confidence_exactly_threshold(self):
        f = Finding(
            severity=Severity.LOW, category=FindingCategory.SECURITY,
            file="t.py", line=1, title="T", evidence="e",
            recommendation="r", confidence=0.8, source="s",
        )
        assert confidence_tier(f) == "high"

    def test_confidence_just_below_high(self):
        f = Finding(
            severity=Severity.LOW, category=FindingCategory.SECURITY,
            file="t.py", line=1, title="T", evidence="e",
            recommendation="r", confidence=0.799, source="s",
        )
        assert confidence_tier(f) == "warning"

    def test_confidence_zero(self):
        f = Finding(
            severity=Severity.LOW, category=FindingCategory.SECURITY,
            file="t.py", line=1, title="T", evidence="e",
            recommendation="r", confidence=0.0, source="s",
        )
        assert confidence_tier(f) == "needs_human_review"

    def test_confidence_one(self):
        f = Finding(
            severity=Severity.LOW, category=FindingCategory.SECURITY,
            file="t.py", line=1, title="T", evidence="e",
            recommendation="r", confidence=1.0, source="s",
        )
        assert confidence_tier(f) == "high"
