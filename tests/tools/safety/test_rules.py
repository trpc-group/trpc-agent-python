# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Tests for shared rule constants and sanitize_text."""

from __future__ import annotations

from trpc_agent_sdk.tools.safety._rules import sanitize_text


class TestSanitizeText:

    def test_sanitize_openai_key(self):
        result = sanitize_text("api_key=sk-proj1234567890abcdefg")
        assert "sk-" not in result
        assert "[SANITIZED]" in result

    def test_sanitize_github_token(self):
        result = sanitize_text("token=ghp_abcdefghijklmnop12345")
        assert "ghp_" not in result
        assert "[SANITIZED]" in result

    def test_sanitize_private_key_header(self):
        result = sanitize_text("key=-----BEGIN PRIVATE KEY-----")
        assert "BEGIN PRIVATE KEY" not in result
        assert "[SANITIZED]" in result

    def test_sanitize_key_value_pair(self):
        result = sanitize_text("password = hunter2")
        assert "hunter2" not in result
        assert "[SANITIZED]" in result

    def test_clean_text_unchanged(self):
        text = "print('hello world')"
        assert sanitize_text(text) == text

    def test_multiple_secrets(self):
        text = "TOKEN=ghp_abc123 and api_key=sk-xyz789"
        result = sanitize_text(text)
        assert "ghp_" not in result
        assert "sk-" not in result

    def test_no_panic_on_empty(self):
        assert sanitize_text("") == ""

    def test_invalid_extra_pattern_silently_ignored(self):
        """Invalid regex in extra_patterns is silently skipped (re.error)."""
        result = sanitize_text("hello world", extra_patterns=[r"[invalid"])
        assert result == "hello world"
