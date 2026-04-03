# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for ReadTool."""

from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import ReadTool


class TestReadTool:
    """Test suite for ReadTool."""

    @pytest.fixture
    def tool(self):
        """Create ReadTool instance."""
        return ReadTool()

    @pytest.fixture
    def tool_with_cwd(self, tmp_path):
        """Create ReadTool instance with cwd."""
        return ReadTool(cwd=str(tmp_path))

    @pytest.fixture
    def tool_context(self):
        """Create mock InvocationContext."""
        return Mock(spec=InvocationContext)

    @pytest.mark.asyncio
    async def test_read_entire_file(self, tool, tool_context, tmp_path):
        """Test reading entire file."""
        test_file = tmp_path / "test.txt"
        content = "Line 1\nLine 2\nLine 3\n"
        test_file.write_text(content)

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"path": str(test_file)},
        )

        assert result["success"] is True
        assert "Line 1" in result["content"]
        assert "Line 2" in result["content"]
        assert "Line 3" in result["content"]
        assert result["total_lines"] == 3
        assert result["read_range"] == "1-3"

    @pytest.mark.asyncio
    async def test_read_line_range(self, tool, tool_context, tmp_path):
        """Test reading specific line range."""
        test_file = tmp_path / "test.txt"
        content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n"
        test_file.write_text(content)

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "start_line": 2,
                "end_line": 4
            },
        )

        assert result["success"] is True
        assert "Line 1" not in result["content"]
        assert "Line 2" in result["content"]
        assert "Line 3" in result["content"]
        assert "Line 4" in result["content"]
        assert "Line 5" not in result["content"]
        assert result["read_range"] == "2-4"

    @pytest.mark.asyncio
    async def test_read_with_relative_path(self, tool_with_cwd, tool_context, tmp_path):
        """Test reading file with relative path."""
        test_file = tmp_path / "test.txt"
        content = "Test content\n"
        test_file.write_text(content)

        result = await tool_with_cwd._run_async_impl(
            tool_context=tool_context,
            args={"path": "test.txt"},
        )

        assert result["success"] is True
        assert "Test content" in result["content"]

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, tool, tool_context):
        """Test reading non-existent file."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"path": "/nonexistent/file.txt"},
        )

        assert "error" in result
        assert "FILE_NOT_FOUND" in result["error"]

    @pytest.mark.asyncio
    async def test_read_directory(self, tool, tool_context, tmp_path):
        """Test reading directory (should fail)."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"path": str(test_dir)},
        )

        assert "error" in result
        assert "INVALID_PATH" in result["error"]

    @pytest.mark.asyncio
    async def test_read_missing_path(self, tool, tool_context):
        """Test reading without path parameter."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={},
        )

        assert "error" in result
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_read_invalid_start_line(self, tool, tool_context, tmp_path):
        """Test reading with invalid start_line."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "start_line": 0
            },
        )

        assert "error" in result
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_read_out_of_range(self, tool, tool_context, tmp_path):
        """Test reading with out-of-range line numbers."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "start_line": 100
            },
        )

        assert "error" in result
        assert "OUT_OF_RANGE" in result["error"]

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tool, tool_context, tmp_path):
        """Test reading empty file."""
        test_file = tmp_path / "empty.txt"
        test_file.write_text("")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"path": str(test_file)},
        )

        assert "error" in result
        assert "OUT_OF_RANGE" in result["error"]

    @pytest.mark.asyncio
    async def test_read_single_line(self, tool, tool_context, tmp_path):
        """Test reading single line."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Single line\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "start_line": 1,
                "end_line": 1
            },
        )

        assert result["success"] is True
        assert result["read_range"] == "1-1"
        assert "Single line" in result["content"]

    def test_calculate_line_range(self, tool):
        """Test _calculate_line_range method."""
        # Test default range
        start, end = tool._calculate_line_range(10, None, None)
        assert start == 1
        assert end == 10

        # Test specific range
        start, end = tool._calculate_line_range(10, 3, 7)
        assert start == 3
        assert end == 7

        # Test range clamping
        start, end = tool._calculate_line_range(10, 0, 20)
        assert start == 1
        assert end == 10

        # Test invalid range (start > end)
        start, end = tool._calculate_line_range(10, 7, 3)
        assert start == 7
        assert end == 7
