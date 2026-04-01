# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for WriteTool."""

from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import WriteTool


class TestWriteTool:
    """Test suite for WriteTool."""

    @pytest.fixture
    def tool(self):
        """Create WriteTool instance."""
        return WriteTool()

    @pytest.fixture
    def tool_with_cwd(self, tmp_path):
        """Create WriteTool instance with cwd."""
        return WriteTool(cwd=str(tmp_path))

    @pytest.fixture
    def tool_context(self):
        """Create mock InvocationContext."""
        return Mock(spec=InvocationContext)

    @pytest.mark.asyncio
    async def test_write_new_file(self, tool, tool_context, tmp_path):
        """Test writing new file."""
        test_file = tmp_path / "new_file.txt"
        content = "This is new content\nLine 2\n"

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "content": content
            },
        )

        assert result["success"] is True
        assert result["action"] == "written to"
        assert result["file_existed"] is False
        assert test_file.exists()
        assert test_file.read_text() == content

    @pytest.mark.asyncio
    async def test_write_overwrite_file(self, tool, tool_context, tmp_path):
        """Test overwriting existing file."""
        test_file = tmp_path / "existing.txt"
        original_content = "Original content\n"
        test_file.write_text(original_content)

        new_content = "New content\n"
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "content": new_content
            },
        )

        assert result["success"] is True
        assert result["action"] == "written to"
        assert result["file_existed"] is True
        assert test_file.read_text() == new_content

    @pytest.mark.asyncio
    async def test_write_append(self, tool, tool_context, tmp_path):
        """Test appending to file."""
        test_file = tmp_path / "append.txt"
        original_content = "Original\n"
        test_file.write_text(original_content)

        append_content = "Appended\n"
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "content": append_content,
                "append": True
            },
        )

        assert result["success"] is True
        assert result["action"] == "appended to"
        assert test_file.read_text() == original_content + append_content

    @pytest.mark.asyncio
    async def test_write_create_directory(self, tool, tool_context, tmp_path):
        """Test creating file in non-existent directory."""
        test_file = tmp_path / "subdir" / "nested" / "file.txt"
        content = "Content\n"

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "content": content
            },
        )

        assert result["success"] is True
        assert test_file.exists()
        assert test_file.read_text() == content

    @pytest.mark.asyncio
    async def test_write_with_relative_path(self, tool_with_cwd, tool_context, tmp_path):
        """Test writing file with relative path."""
        content = "Relative path content\n"

        result = await tool_with_cwd._run_async_impl(
            tool_context=tool_context,
            args={
                "path": "relative.txt",
                "content": content
            },
        )

        assert result["success"] is True
        assert (tmp_path / "relative.txt").exists()
        assert (tmp_path / "relative.txt").read_text() == content

    @pytest.mark.asyncio
    async def test_write_missing_path(self, tool, tool_context):
        """Test writing without path parameter."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"content": "content"},
        )

        assert "error" in result
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_write_missing_content(self, tool, tool_context, tmp_path):
        """Test writing without content parameter."""
        test_file = tmp_path / "test.txt"

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"path": str(test_file)},
        )

        assert "error" in result
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_write_invalid_content_type(self, tool, tool_context, tmp_path):
        """Test writing with invalid content type."""
        test_file = tmp_path / "test.txt"

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "content": 123
            },
        )

        assert "error" in result
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_write_empty_content(self, tool, tool_context, tmp_path):
        """Test writing empty content."""
        test_file = tmp_path / "empty.txt"

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "content": ""
            },
        )

        assert result["success"] is True
        assert test_file.exists()
        assert test_file.read_text() == ""

    @pytest.mark.asyncio
    async def test_write_multiline_content(self, tool, tool_context, tmp_path):
        """Test writing multiline content."""
        test_file = tmp_path / "multiline.txt"
        content = "Line 1\nLine 2\nLine 3\n"

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "path": str(test_file),
                "content": content
            },
        )

        assert result["success"] is True
        assert test_file.read_text() == content
        assert result["bytes_written"] == len(content.encode("utf-8"))
