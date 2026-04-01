# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for file utilities, especially encoding detection."""

import pytest
from trpc_agent_sdk.tools.file_tools._file_utils import _detect_encoding
from trpc_agent_sdk.tools.file_tools._file_utils import safe_read_file


class TestFileUtils:
    """Test suite for file utilities."""

    @pytest.mark.asyncio
    async def test_detect_encoding_utf8_with_chinese(self, tmp_path):
        """Test detecting UTF-8 encoding with Chinese characters."""
        test_file = tmp_path / "utf8_chinese.txt"
        content = "Hello, World! 你好世界\n这是UTF-8编码测试"
        test_file.write_text(content, encoding="utf-8")

        detected = _detect_encoding(str(test_file))
        assert detected == "utf-8"

        # Verify we can read with detected encoding
        with open(test_file, "r", encoding=detected) as f:
            read_content = f.read()
        assert read_content == content

    @pytest.mark.asyncio
    async def test_detect_encoding_ascii(self, tmp_path):
        """Test detecting ASCII encoding."""
        test_file = tmp_path / "ascii.txt"
        content = "Hello, World!\nASCII text only"
        test_file.write_text(content, encoding="ascii")

        detected = _detect_encoding(str(test_file))
        # ASCII files may be detected as ascii or utf-8, both are valid
        assert detected in ["ascii", "utf-8"]

        # Verify we can read with detected encoding
        with open(test_file, "r", encoding=detected) as f:
            read_content = f.read()
        assert read_content == content

    @pytest.mark.asyncio
    async def test_detect_encoding_empty_file(self, tmp_path):
        """Test detecting encoding of empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("", encoding="utf-8")

        detected = _detect_encoding(str(test_file))
        assert detected == "utf-8"  # Default for empty files

    @pytest.mark.asyncio
    async def test_detect_encoding_utf8_english_only(self, tmp_path):
        """Test detecting UTF-8 encoding with English only."""
        test_file = tmp_path / "utf8_english.txt"
        content = "Hello, World!\nPure English text"
        test_file.write_text(content, encoding="utf-8")

        detected = _detect_encoding(str(test_file))
        # English-only UTF-8 may be detected as ascii or utf-8
        assert detected in ["ascii", "utf-8"]

        # Verify we can read with detected encoding
        with open(test_file, "r", encoding=detected) as f:
            read_content = f.read()
        assert read_content == content

    @pytest.mark.asyncio
    async def test_safe_read_file_utf8(self, tmp_path):
        """Test safe_read_file with UTF-8 file."""
        test_file = tmp_path / "test_utf8.txt"
        content = "Hello, World! 你好世界\nUTF-8 content"
        test_file.write_text(content, encoding="utf-8")

        read_content, encoding = safe_read_file(str(test_file))
        assert encoding == "utf-8"
        assert read_content == content

    @pytest.mark.asyncio
    async def test_safe_read_file_with_specified_encoding(self, tmp_path):
        """Test safe_read_file with specified encoding."""
        test_file = tmp_path / "test_utf8.txt"
        content = "Hello, World! 你好世界\nUTF-8 content"
        test_file.write_text(content, encoding="utf-8")

        read_content, encoding = safe_read_file(str(test_file), encoding="utf-8")
        assert encoding == "utf-8"
        assert read_content == content

    @pytest.mark.asyncio
    async def test_safe_read_file_auto_detect(self, tmp_path):
        """Test safe_read_file with auto-detection."""
        test_file = tmp_path / "test_auto.txt"
        content = "Hello, World! 你好世界\nAuto detect encoding"
        test_file.write_text(content, encoding="utf-8")

        read_content, encoding = safe_read_file(str(test_file))
        # Should detect utf-8 or ascii
        assert encoding in ["utf-8", "ascii"]
        assert read_content == content

    @pytest.mark.asyncio
    async def test_safe_read_file_not_found(self):
        """Test safe_read_file with non-existent file."""
        with pytest.raises(Exception, match="Error reading file"):
            safe_read_file("/nonexistent/file.txt")

    @pytest.mark.asyncio
    async def test_encoding_normalization(self, tmp_path):
        """Test that encoding names are normalized (utf_8 -> utf-8)."""
        test_file = tmp_path / "test_normalize.txt"
        content = "Test content\n"
        test_file.write_text(content, encoding="utf-8")

        detected = _detect_encoding(str(test_file))
        # Should be normalized to utf-8 (not utf_8)
        assert detected == "utf-8" or detected == "ascii"

        # Verify normalization works with Python's open()
        with open(test_file, "r", encoding=detected) as f:
            read_content = f.read()
        assert read_content == content
