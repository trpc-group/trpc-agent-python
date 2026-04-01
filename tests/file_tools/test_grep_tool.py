# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for GrepTool."""

from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import GrepTool


class TestGrepTool:
    """Test suite for GrepTool."""

    @pytest.fixture
    def tool(self):
        """Create GrepTool instance."""
        return GrepTool()

    @pytest.fixture
    def tool_with_cwd(self, tmp_path):
        """Create GrepTool instance with cwd."""
        return GrepTool(cwd=str(tmp_path))

    @pytest.fixture
    def tool_context(self):
        """Create mock InvocationContext."""
        return Mock(spec=InvocationContext)

    @pytest.mark.asyncio
    async def test_grep_single_file(self, tool, tool_context, tmp_path):
        """Test searching in single file."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2 with pattern\nLine 3\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "pattern",
                "path": str(test_file)
            },
        )

        assert result["success"] is True
        assert result["total_matches"] == 1
        assert len(result["matches"]) == 1
        assert "pattern" in result["formatted_output"]

    @pytest.mark.asyncio
    async def test_grep_directory(self, tool, tool_context, tmp_path):
        """Test searching in directory."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        (test_dir / "file1.txt").write_text("Line with pattern\n")
        (test_dir / "file2.txt").write_text("No match\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "pattern",
                "path": str(test_dir)
            },
        )

        assert result["success"] is True
        assert result["total_matches"] == 1

    @pytest.mark.asyncio
    async def test_grep_case_sensitive(self, tool, tool_context, tmp_path):
        """Test case-sensitive search."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Pattern\npattern\nPATTERN\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "Pattern",
                "path": str(test_file),
                "case_sensitive": True
            },
        )

        assert result["success"] is True
        assert result["total_matches"] == 1

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self, tool, tool_context, tmp_path):
        """Test case-insensitive search."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Pattern\npattern\nPATTERN\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "pattern",
                "path": str(test_file),
                "case_sensitive": False
            },
        )

        assert result["success"] is True
        assert result["total_matches"] == 3

    @pytest.mark.asyncio
    async def test_grep_regex_pattern(self, tool, tool_context, tmp_path):
        """Test regex pattern search."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("key1=value1\nkey2=value2\nnot_a_key\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": r"key\d+=",
                "path": str(test_file)
            },
        )

        assert result["success"] is True
        assert result["total_matches"] == 2

    @pytest.mark.asyncio
    async def test_grep_no_matches(self, tool, tool_context, tmp_path):
        """Test search with no matches."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line 1\nLine 2\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "nonexistent",
                "path": str(test_file)
            },
        )

        assert result["success"] is True
        assert result["total_matches"] == 0
        assert "found 0 results" in result["formatted_output"]

    @pytest.mark.asyncio
    async def test_grep_missing_pattern(self, tool, tool_context):
        """Test search without pattern parameter."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"path": "."},
        )

        assert "error" in result
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_grep_invalid_regex(self, tool, tool_context, tmp_path):
        """Test search with invalid regex."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Content\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "[invalid",
                "path": str(test_file)
            },
        )

        assert "error" in result
        assert "INVALID_REGEX" in result["error"]

    @pytest.mark.asyncio
    async def test_grep_path_not_found(self, tool, tool_context):
        """Test search with non-existent path."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "pattern",
                "path": "/nonexistent/path"
            },
        )

        assert "error" in result
        assert "PATH_NOT_FOUND" in result["error"]

    @pytest.mark.asyncio
    async def test_grep_max_results(self, tool, tool_context, tmp_path):
        """Test search with max_results limit."""
        test_file = tmp_path / "test.txt"
        content = "\n".join([f"Line {i} with pattern" for i in range(100)])
        test_file.write_text(content)

        tool_with_limit = GrepTool(max_results=10)
        result = await tool_with_limit._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "pattern",
                "path": str(test_file)
            },
        )

        assert result["success"] is True
        assert result["total_matches"] <= 10

    @pytest.mark.asyncio
    async def test_grep_with_relative_path(self, tool_with_cwd, tool_context, tmp_path):
        """Test search with relative path."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("Line with pattern\n")

        result = await tool_with_cwd._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "pattern",
                "path": "test.txt"
            },
        )

        assert result["success"] is True
        assert result["total_matches"] == 1

    @pytest.mark.asyncio
    async def test_grep_skip_directories(self, tool, tool_context, tmp_path):
        """Test that grep skips .git and other directories."""
        test_dir = tmp_path / "test_dir"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("pattern\n")
        (test_dir / ".git").mkdir()
        (test_dir / ".git" / "file.txt").write_text("pattern\n")

        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": "pattern",
                "path": str(test_dir)
            },
        )

        assert result["success"] is True
        assert result["total_matches"] == 1
