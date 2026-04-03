# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Unit tests for EditTool."""

from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import EditTool


class TestEditTool:
    """Test suite for EditTool."""

    @pytest.fixture
    def tool(self):
        """Create EditTool instance."""
        return EditTool()

    @pytest.fixture
    def tool_with_cwd(self, tmp_path):
        """Create EditTool instance with cwd."""
        return EditTool(cwd=str(tmp_path))

    @pytest.fixture
    def tool_context(self):
        """Create mock InvocationContext."""
        return Mock(spec=InvocationContext)

    @pytest.mark.asyncio
    async def test_edit_single_line(self, tool, tool_context, tmp_path):
        """Test editing single line."""
        test_file = tmp_path / "test.txt"
        original_content = "Line 1\nLine 2\nLine 3\n"
        test_file.write_text(original_content)

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "old_string": "Line 2",
                "new_string": "Line 2 Modified",
            },
        )

        assert result["success"] is True
        assert "Line 2 Modified" in test_file.read_text()
        assert "Line 1" in test_file.read_text()
        assert "Line 3" in test_file.read_text()

    @pytest.mark.asyncio
    async def test_edit_multiline_block(self, tool, tool_context, tmp_path):
        """Test editing multiline block."""
        test_file = tmp_path / "test.txt"
        original_content = "Line 1\nLine 2\nLine 3\nLine 4\n"
        test_file.write_text(original_content)

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "old_string": "Line 2\nLine 3",
                "new_string": "Line 2 Modified\nLine 3 Modified",
            },
        )

        assert result["success"] is True
        content = test_file.read_text()
        assert "Line 2 Modified" in content
        assert "Line 3 Modified" in content
        assert "Line 1" in content
        assert "Line 4" in content

    @pytest.mark.asyncio
    async def test_edit_with_indentation(self, tool, tool_context, tmp_path):
        """Test editing with indentation."""
        test_file = tmp_path / "test.py"
        original_content = "def func():\n    return 1\n"
        test_file.write_text(original_content)

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "old_string": "    return 1",
                "new_string": "    return 2",
            },
        )

        assert result["success"] is True
        assert "return 2" in test_file.read_text()

    @pytest.mark.asyncio
    async def test_edit_file_not_found(self, tool, tool_context):
        """Test editing non-existent file."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": "/nonexistent/file.txt",
                "old_string": "old",
                "new_string": "new",
            },
        )

        assert result["success"] is False
        assert "FILE_NOT_FOUND" in result["error"]

    @pytest.mark.asyncio
    async def test_edit_no_match(self, tool, tool_context, tmp_path):
        """Test editing with no matching content."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "old_string": "Non-existent content",
                "new_string": "New content",
            },
        )

        assert result["success"] is False
        assert "exact match not found" in result["error"]

    @pytest.mark.asyncio
    async def test_edit_multiple_matches(self, tool, tool_context, tmp_path):
        """Test editing with multiple matches (should fail)."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\nLine 1\nLine 2\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "old_string": "Line 1\nLine 2",
                "new_string": "Modified",
            },
        )

        assert result["success"] is False
        assert "multiple matches found" in result["error"]

    @pytest.mark.asyncio
    async def test_edit_missing_path(self, tool, tool_context):
        """Test editing without path parameter."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "old_string": "old",
                "new_string": "new"
            },
        )

        assert result["success"] is False
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_edit_empty_old_string(self, tool, tool_context, tmp_path):
        """Test editing with empty old_string."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Content\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "old_string": "",
                "new_string": "new"
            },
        )

        assert result["success"] is False
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_edit_with_relative_path(self, tool_with_cwd, tool_context, tmp_path):
        """Test editing file with relative path."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Old content\n")

        result = await tool_with_cwd._run_async_impl(
            tool_context=tool_context,
            args={
                "path": "test.txt",
                "old_string": "Old content",
                "new_string": "New content",
            },
        )

        assert result["success"] is True
        assert (tmp_path / "test.txt").read_text() == "New content\n"

    @pytest.mark.asyncio
    async def test_edit_preserve_newline(self, tool, tool_context, tmp_path):
        """Test that edit preserves newline at end of file."""
        test_file = tmp_path / "test.txt"
        original_content = "Line 1\nLine 2\n"
        test_file.write_text(original_content)

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "old_string": "Line 1",
                "new_string": "Line 1 Modified",
            },
        )

        assert result["success"] is True
        content = test_file.read_text()
        assert content.endswith("\n")

    @pytest.mark.asyncio
    async def test_edit_with_tabs(self, tool, tool_context, tmp_path):
        """Test editing with tabs (should expand tabs)."""
        test_file = tmp_path / "test.txt"
        original_content = "\tLine 1\n\tLine 2\n"
        test_file.write_text(original_content)

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "old_string": "    Line 1",
                "new_string": "    Line 1 Modified",
            },
        )

        assert result["success"] is True
        assert "Line 1 Modified" in test_file.read_text()
