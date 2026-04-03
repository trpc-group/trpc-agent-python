# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for file_tools/_file_utils.py."""

import os
import tempfile

from trpc_agent_sdk.tools.file_tools._file_utils import (
    _detect_encoding,
    _detect_encoding_fallback,
    safe_read_file,
)


class TestDetectEncoding:
    """Test suite for _detect_encoding."""

    def test_utf8_file(self, tmp_path):
        """Test UTF-8 file detected."""
        p = tmp_path / "test.txt"
        p.write_text("hello world", encoding="utf-8")
        result = _detect_encoding(str(p))
        assert result.lower().replace("-", "").replace("_", "") in ("utf8", "ascii")

    def test_empty_file(self, tmp_path):
        """Test empty file returns utf-8."""
        p = tmp_path / "empty.txt"
        p.write_bytes(b"")
        assert _detect_encoding(str(p)) == "utf-8"

    def test_nonexistent_file(self):
        """Test nonexistent file returns utf-8 via fallback."""
        result = _detect_encoding("/nonexistent/path/12345")
        assert result == "utf-8"


class TestDetectEncodingFallback:
    """Test suite for _detect_encoding_fallback."""

    def test_utf8_content(self, tmp_path):
        """Test UTF-8 content detected."""
        p = tmp_path / "test.txt"
        p.write_bytes("hello world".encode("utf-8"))
        assert _detect_encoding_fallback(str(p)) == "utf-8"

    def test_nonexistent_returns_utf8(self):
        """Test nonexistent file returns utf-8."""
        result = _detect_encoding_fallback("/nonexistent/path/12345")
        assert result == "utf-8"


class TestSafeReadFile:
    """Test suite for safe_read_file."""

    def test_read_utf8(self, tmp_path):
        """Test reading UTF-8 file."""
        p = tmp_path / "test.txt"
        p.write_text("hello world", encoding="utf-8")
        content, enc = safe_read_file(str(p))
        assert content == "hello world"
        assert enc == "utf-8"

    def test_read_with_encoding_fallback(self, tmp_path):
        """Test reading with encoding detection fallback."""
        p = tmp_path / "test.txt"
        p.write_bytes("hello".encode("utf-8"))
        content, enc = safe_read_file(str(p), encoding="utf-8")
        assert content == "hello"
