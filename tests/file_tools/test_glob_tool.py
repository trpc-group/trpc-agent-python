# -*- coding: utf-8 -*-
#
# Copyright @ 2026 Tencent.com
"""Unit tests for GlobTool."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.tools import GlobTool


class TestGlobTool:
    """Test suite for GlobTool."""

    @pytest.fixture
    def tool(self):
        """Create GlobTool instance."""
        return GlobTool()

    @pytest.fixture
    def tool_with_cwd(self, tmp_path):
        """Create GlobTool instance with cwd."""
        return GlobTool(cwd=str(tmp_path))

    @pytest.fixture
    def tool_context(self):
        """Create mock InvocationContext."""
        return Mock(spec=InvocationContext)

    @pytest.fixture
    def test_files(self, tmp_path):
        """Create test files structure."""
        (tmp_path / "file1.txt").write_text("content1")
        (tmp_path / "file2.txt").write_text("content2")
        (tmp_path / "file3.py").write_text("content3")
        (tmp_path / "subdir").mkdir()
        (tmp_path / "subdir" / "file4.txt").write_text("content4")
        return tmp_path

    @pytest.mark.asyncio
    async def test_glob_simple_pattern(self, tool, tool_context, test_files):
        """Test glob with simple pattern."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"pattern": str(test_files / "*.txt")},
        )

        assert result["success"] is True
        assert result["count"] == 2
        assert any("file1.txt" in m for m in result["matches"])
        assert any("file2.txt" in m for m in result["matches"])

    @pytest.mark.asyncio
    async def test_glob_recursive_pattern(self, tool, tool_context, test_files):
        """Test glob with recursive pattern."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"pattern": str(test_files / "**" / "*.txt")},
        )

        assert result["success"] is True
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_glob_brace_expansion(self, tool, tool_context, test_files):
        """Test glob with brace expansion."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"pattern": str(test_files / "*.{txt,py}")},
        )

        assert result["success"] is True
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_glob_include_dirs(self, tool, tool_context, test_files):
        """Test glob including directories."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": str(test_files / "*"),
                "include_dirs": True
            },
        )

        assert result["success"] is True
        assert result["count"] >= 4

    @pytest.mark.asyncio
    async def test_glob_exclude_dirs(self, tool, tool_context, test_files):
        """Test glob excluding directories."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": str(test_files / "*"),
                "include_dirs": False
            },
        )

        assert result["success"] is True
        assert all(Path(m).is_file() for m in result["matches"])

    @pytest.mark.asyncio
    async def test_glob_max_results(self, tool, tool_context, test_files):
        """Test glob with max_results limit."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={
                "pattern": str(test_files / "*"),
                "max_results": 2
            },
        )

        assert result["success"] is True
        assert result["count"] <= 2
        assert result["truncated"] is True

    @pytest.mark.asyncio
    async def test_glob_missing_pattern(self, tool, tool_context):
        """Test glob without pattern parameter."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={},
        )

        assert "error" in result
        assert "INVALID_PARAMETER" in result["error"]

    @pytest.mark.asyncio
    async def test_glob_no_matches(self, tool, tool_context, tmp_path):
        """Test glob with no matches."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"pattern": str(tmp_path / "*.nonexistent")},
        )

        assert result["success"] is True
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_glob_with_relative_path(self, tool_with_cwd, tool_context, test_files):
        """Test glob with relative path."""
        result = await tool_with_cwd._run_async_impl(
            tool_context=tool_context,
            args={"pattern": "*.txt"},
        )

        assert result["success"] is True
        assert result["count"] == 2

    @pytest.mark.asyncio
    async def test_glob_deduplication(self, tool, tool_context, test_files):
        """Test that glob deduplicates results."""
        result = await tool._run_async_impl(
            tool_context=tool_context,
            args={"pattern": str(test_files / "*.txt")},
        )

        assert result["success"] is True
        assert len(result["matches"]) == len(set(result["matches"]))

    def test_expand_brace_pattern(self, tool):
        """Test _expand_brace_pattern method."""
        patterns = tool._expand_brace_pattern("*.{txt,py}")
        assert "*.txt" in patterns
        assert "*.py" in patterns

        patterns = tool._expand_brace_pattern("**/*.{py,{go,js}}")
        assert len(patterns) >= 2

        patterns = tool._expand_brace_pattern("*.txt")
        assert patterns == ["*.txt"]
