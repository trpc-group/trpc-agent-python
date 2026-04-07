"""Unit tests for trpc_agent_sdk.server.openclaw.tools.filesystem module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from trpc_agent_sdk.context import InvocationContext
from trpc_agent_sdk.server.openclaw.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
    _find_match,
    _not_found_msg,
    _resolve_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool_context() -> InvocationContext:
    ctx = MagicMock(spec=InvocationContext)
    ctx.agent_context = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# _resolve_path
# ---------------------------------------------------------------------------


class TestResolvePath:

    def test_relative_with_workspace(self, tmp_path):
        result = _resolve_path("sub/file.txt", workspace=tmp_path)
        assert result == (tmp_path / "sub" / "file.txt").resolve()

    def test_absolute_ignores_workspace(self, tmp_path):
        result = _resolve_path("/absolute/path.txt", workspace=tmp_path)
        assert result == Path("/absolute/path.txt").resolve()

    def test_no_workspace_resolves_relative(self):
        result = _resolve_path("file.txt")
        assert result.is_absolute()

    def test_allowed_dir_inside(self, tmp_path):
        (tmp_path / "sub").mkdir()
        result = _resolve_path("sub/file.txt", workspace=tmp_path, allowed_dir=tmp_path)
        assert result.is_absolute()

    def test_allowed_dir_outside_raises(self, tmp_path):
        with pytest.raises(PermissionError, match="outside allowed directory"):
            _resolve_path("/etc/passwd", allowed_dir=tmp_path)

    def test_home_expansion(self, tmp_path):
        result = _resolve_path("~/somefile", workspace=tmp_path)
        assert result.is_absolute()
        assert "~" not in str(result)


# ---------------------------------------------------------------------------
# _find_match
# ---------------------------------------------------------------------------


class TestFindMatch:

    def test_exact_match(self):
        content = "line1\nline2\nline3"
        match, count = _find_match(content, "line2")
        assert match == "line2"
        assert count == 1

    def test_exact_match_multiple(self):
        content = "abc\ndef\nabc\nghi"
        match, count = _find_match(content, "abc")
        assert match == "abc"
        assert count == 2

    def test_whitespace_trimmed_match(self):
        content = "  hello  \n  world  "
        old_text = "hello\nworld"
        match, count = _find_match(content, old_text)
        assert match is not None
        assert count == 1
        assert "hello" in match

    def test_no_match(self):
        content = "line1\nline2"
        match, count = _find_match(content, "nonexistent")
        assert match is None
        assert count == 0

    def test_empty_old_text(self):
        content = "something"
        match, count = _find_match(content, "")
        assert match == ""
        assert count >= 1

    def test_empty_old_text_lines(self):
        content = "something"
        match, count = _find_match(content, "\n")
        assert match is None or count == 0

    def test_multiline_exact(self):
        content = "line1\nline2\nline3\nline4"
        match, count = _find_match(content, "line2\nline3")
        assert match == "line2\nline3"
        assert count == 1

    def test_trimmed_match_multiple_candidates(self):
        content = "  a  \n  b  \n  a  \n  b  "
        old_text = "a\nb"
        match, count = _find_match(content, old_text)
        assert match is not None
        assert count == 2


# ---------------------------------------------------------------------------
# _not_found_msg
# ---------------------------------------------------------------------------


class TestNotFoundMsg:

    def test_similar_text_found(self):
        content = "hello world\nfoo bar\nbaz qux"
        old_text = "hello worlds\nfoo bar"
        result = _not_found_msg(old_text, content, "test.txt")
        assert "not found" in result
        assert "similar" in result
        assert "test.txt" in result

    def test_no_similar_text(self):
        content = "aaaa bbbb cccc"
        old_text = "xxxx yyyy zzzz totally different content"
        result = _not_found_msg(old_text, content, "test.txt")
        assert "not found" in result
        assert "No similar text" in result

    def test_best_match_reports_line_number(self):
        content = "line1\nline2\nline3\nhello world"
        old_text = "hello worlds"
        result = _not_found_msg(old_text, content, "f.py")
        assert "not found" in result


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------


class TestReadFileTool:

    async def test_file_not_found(self, tmp_path):
        tool = ReadFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "nonexistent.txt"},
        )
        assert "not found" in result

    async def test_not_a_file(self, tmp_path):
        d = tmp_path / "subdir"
        d.mkdir()
        tool = ReadFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(d)},
        )
        assert "Not a file" in result

    async def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        tool = ReadFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "empty.txt"},
        )
        assert "Empty file" in result

    async def test_read_with_lines(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("line1\nline2\nline3\nline4\nline5\n")
        tool = ReadFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "file.txt"},
        )
        assert "1| line1" in result
        assert "End of file" in result

    async def test_pagination_offset(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("\n".join(f"line{i}" for i in range(1, 11)))
        tool = ReadFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "file.txt", "offset": 3, "limit": 2},
        )
        assert "3| line3" in result
        assert "4| line4" in result
        assert "Showing lines 3-4" in result

    async def test_offset_beyond_end(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("line1\nline2")
        tool = ReadFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "file.txt", "offset": 100},
        )
        assert "beyond end" in result

    async def test_max_chars_truncation(self, tmp_path):
        f = tmp_path / "big.txt"
        lines = [f"{'x' * 200}" for _ in range(1000)]
        f.write_text("\n".join(lines))
        tool = ReadFileTool(workspace=tmp_path)
        tool._MAX_CHARS = 500
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "big.txt"},
        )
        assert "Showing lines" in result

    async def test_permission_error(self, tmp_path):
        tool = ReadFileTool(workspace=tmp_path, allowed_dir=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "/etc/passwd"},
        )
        assert "Error" in result

    async def test_declaration(self):
        tool = ReadFileTool()
        decl = tool._get_declaration()
        assert decl.name == "read_file"
        assert "path" in decl.parameters.required


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------


class TestWriteFileTool:

    async def test_write_success(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "new_file.txt", "content": "hello world"},
        )
        assert "Successfully wrote" in result
        assert (tmp_path / "new_file.txt").read_text() == "hello world"

    async def test_write_creates_parent_dirs(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "deep/nested/file.txt", "content": "data"},
        )
        assert "Successfully wrote" in result
        assert (tmp_path / "deep" / "nested" / "file.txt").exists()

    async def test_write_permission_error(self, tmp_path):
        tool = WriteFileTool(workspace=tmp_path, allowed_dir=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "/etc/shadow", "content": "bad"},
        )
        assert "Error" in result

    async def test_declaration(self):
        tool = WriteFileTool()
        decl = tool._get_declaration()
        assert decl.name == "write_file"
        assert "path" in decl.parameters.required


# ---------------------------------------------------------------------------
# EditFileTool
# ---------------------------------------------------------------------------


class TestEditFileTool:

    async def test_edit_success(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("hello world")
        tool = EditFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "file.txt", "old_text": "hello", "new_text": "goodbye"},
        )
        assert "Successfully edited" in result
        assert f.read_text() == "goodbye world"

    async def test_edit_file_not_found(self, tmp_path):
        tool = EditFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "missing.txt", "old_text": "a", "new_text": "b"},
        )
        assert "not found" in result

    async def test_edit_old_text_not_found(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("some content")
        tool = EditFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "file.txt", "old_text": "nonexistent text here", "new_text": "b"},
        )
        assert "not found" in result

    async def test_edit_multiple_occurrences_warning(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("abc def abc ghi")
        tool = EditFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "file.txt", "old_text": "abc", "new_text": "xyz"},
        )
        assert "appears 2 times" in result

    async def test_edit_replace_all(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("abc def abc ghi")
        tool = EditFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={
                "path": "file.txt",
                "old_text": "abc",
                "new_text": "xyz",
                "replace_all": True,
            },
        )
        assert "Successfully edited" in result
        assert f.read_text() == "xyz def xyz ghi"

    async def test_edit_crlf_handling(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_bytes(b"hello\r\nworld\r\n")
        tool = EditFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "file.txt", "old_text": "hello", "new_text": "goodbye"},
        )
        assert "Successfully edited" in result
        content = f.read_bytes()
        assert b"\r\n" in content
        assert b"goodbye" in content

    async def test_edit_whitespace_fuzzy_match(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("  hello  \n  world  ")
        tool = EditFileTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "file.txt", "old_text": "hello\nworld", "new_text": "foo\nbar"},
        )
        assert "Successfully edited" in result

    async def test_edit_permission_error(self, tmp_path):
        tool = EditFileTool(workspace=tmp_path, allowed_dir=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "/etc/passwd", "old_text": "a", "new_text": "b"},
        )
        assert "Error" in result

    async def test_declaration(self):
        tool = EditFileTool()
        decl = tool._get_declaration()
        assert decl.name == "edit_file"
        assert "path" in decl.parameters.required


# ---------------------------------------------------------------------------
# ListDirTool
# ---------------------------------------------------------------------------


class TestListDirTool:

    async def test_empty_dir(self, tmp_path):
        d = tmp_path / "empty_dir"
        d.mkdir()
        tool = ListDirTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(d)},
        )
        assert "empty" in result.lower()

    async def test_non_recursive_listing(self, tmp_path):
        (tmp_path / "file1.txt").touch()
        (tmp_path / "file2.txt").touch()
        (tmp_path / "subdir").mkdir()
        tool = ListDirTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(tmp_path)},
        )
        assert "F file1.txt" in result
        assert "F file2.txt" in result
        assert "D subdir" in result

    async def test_recursive_listing(self, tmp_path):
        (tmp_path / "file1.txt").touch()
        sub = tmp_path / "subdir"
        sub.mkdir()
        (sub / "nested.txt").touch()
        tool = ListDirTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(tmp_path), "recursive": True},
        )
        assert "file1.txt" in result
        assert "nested.txt" in result

    async def test_ignored_dirs_filtered(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "src").mkdir()
        tool = ListDirTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(tmp_path)},
        )
        assert ".git" not in result
        assert "node_modules" not in result
        assert "__pycache__" not in result
        assert "src" in result

    async def test_max_entries_truncation(self, tmp_path):
        for i in range(10):
            (tmp_path / f"file{i:02d}.txt").touch()
        tool = ListDirTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(tmp_path), "max_entries": 3},
        )
        assert "truncated" in result
        assert "3 of 10" in result

    async def test_dir_not_found(self, tmp_path):
        tool = ListDirTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(tmp_path / "nonexistent")},
        )
        assert "not found" in result

    async def test_not_a_directory(self, tmp_path):
        f = tmp_path / "file.txt"
        f.touch()
        tool = ListDirTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(f)},
        )
        assert "Not a directory" in result

    async def test_recursive_ignores_nested_pycache(self, tmp_path):
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "module.pyc").touch()
        (tmp_path / "real.txt").touch()
        tool = ListDirTool(workspace=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": str(tmp_path), "recursive": True},
        )
        assert "real.txt" in result
        assert "module.pyc" not in result

    async def test_permission_error(self, tmp_path):
        tool = ListDirTool(workspace=tmp_path, allowed_dir=tmp_path)
        ctx = _tool_context()
        result = await tool._run_async_impl(
            tool_context=ctx,
            args={"path": "/etc"},
        )
        assert "Error" in result

    async def test_declaration(self):
        tool = ListDirTool()
        decl = tool._get_declaration()
        assert decl.name == "list_dir"
        assert "path" in decl.parameters.required
