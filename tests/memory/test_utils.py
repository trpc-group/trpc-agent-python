# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for trpc_agent_sdk.memory._utils.

Covers:
- format_timestamp: ISO-format conversion from float timestamps
- extract_words_lower: English word extraction, Chinese character extraction, mixed input
"""

from __future__ import annotations

from datetime import datetime

from trpc_agent_sdk.memory._utils import extract_words_lower, format_timestamp


# ---------------------------------------------------------------------------
# format_timestamp
# ---------------------------------------------------------------------------


class TestFormatTimestamp:
    def test_known_timestamp(self):
        ts = 0.0
        result = format_timestamp(ts)
        expected = datetime.fromtimestamp(0.0).isoformat()
        assert result == expected

    def test_returns_iso_format(self):
        ts = 1700000000.0
        result = format_timestamp(ts)
        assert "T" in result

    def test_fractional_seconds(self):
        ts = 1700000000.123
        result = format_timestamp(ts)
        expected = datetime.fromtimestamp(ts).isoformat()
        assert result == expected

    def test_round_trip(self):
        ts = 1700000000.0
        iso_str = format_timestamp(ts)
        parsed = datetime.fromisoformat(iso_str)
        assert abs(parsed.timestamp() - ts) < 1


# ---------------------------------------------------------------------------
# extract_words_lower
# ---------------------------------------------------------------------------


class TestExtractWordsLower:
    def test_english_only(self):
        result = extract_words_lower("Hello World")
        assert result == {"hello", "world"}

    def test_english_case_insensitive(self):
        result = extract_words_lower("FOO Bar baz")
        assert "foo" in result
        assert "bar" in result
        assert "baz" in result

    def test_chinese_characters(self):
        result = extract_words_lower("你好世界")
        assert "你" in result
        assert "好" in result
        assert "世" in result
        assert "界" in result

    def test_mixed_english_and_chinese(self):
        result = extract_words_lower("Hello你好World世界")
        assert "hello" in result
        assert "world" in result
        assert "你" in result
        assert "好" in result

    def test_empty_string(self):
        result = extract_words_lower("")
        assert result == set()

    def test_numbers_ignored(self):
        result = extract_words_lower("abc 123 def")
        assert "abc" in result
        assert "def" in result
        assert "123" not in result

    def test_special_characters_ignored(self):
        result = extract_words_lower("hello! @world# $test%")
        assert result == {"hello", "world", "test"}

    def test_returns_set(self):
        result = extract_words_lower("hello hello hello")
        assert isinstance(result, set)
        assert result == {"hello"}

    def test_punctuation_between_words(self):
        result = extract_words_lower("hello,world;test")
        assert "hello" in result
        assert "world" in result
        assert "test" in result
