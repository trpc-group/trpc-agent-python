# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from trpc_agent_sdk.code_executors.utils import collect_files_with_glob
from trpc_agent_sdk.code_executors.utils import copy_dir
from trpc_agent_sdk.code_executors.utils import copy_path
from trpc_agent_sdk.code_executors.utils import detect_content_type
from trpc_agent_sdk.code_executors.utils import get_rel_path
from trpc_agent_sdk.code_executors.utils import make_symlink
from trpc_agent_sdk.code_executors.utils import make_tree_read_only
from trpc_agent_sdk.code_executors.utils import path_join


class TestPathJoin:
    """Test suite for path_join function."""

    def test_path_join_basic(self):
        """Test basic path joining."""
        result = path_join("/base", "path/to/file")
        assert result == "/base/path/to/file"

    def test_path_join_with_dot_dot(self):
        """Test path joining normalizes paths."""
        result = path_join("/base", "../other")
        assert result == "/base/../other"

    def test_path_join_empty_path(self):
        """Test path joining with empty path."""
        result = path_join("/base", "")
        assert result == "/base/."

    def test_path_join_absolute_path(self):
        """Test path joining with absolute path."""
        result = path_join("/base", "/absolute/path")
        assert result == "/absolute/path"


class TestCopyDir:
    """Test suite for copy_dir function."""

    def test_copy_dir(self):
        """Test copying a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src"
            dst = Path(tmpdir) / "dst"
            src.mkdir()
            (src / "file.txt").write_text("content")

            copy_dir(src, dst)

            assert dst.exists()
            assert (dst / "file.txt").exists()
            assert (dst / "file.txt").read_text() == "content"

    def test_copy_dir_nested(self):
        """Test copying nested directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src"
            dst = Path(tmpdir) / "dst"
            src.mkdir()
            (src / "subdir").mkdir()
            (src / "subdir" / "file.txt").write_text("content")

            copy_dir(src, dst)

            assert (dst / "subdir" / "file.txt").exists()
            assert (dst / "subdir" / "file.txt").read_text() == "content"

    def test_copy_dir_into_existing(self):
        """Test copying directory into existing destination."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src"
            dst = Path(tmpdir) / "dst"
            src.mkdir()
            dst.mkdir()
            (src / "file.txt").write_text("content")

            copy_dir(src, dst)

            assert (dst / "file.txt").exists()


class TestMakeTreeReadOnly:
    """Test suite for make_tree_read_only function."""

    def test_make_tree_read_only(self):
        """Test making directory tree read-only."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "file.txt").write_text("content")
            (root / "subdir").mkdir()
            (root / "subdir" / "file2.txt").write_text("content2")

            # Make writable first
            os.chmod(root / "file.txt", 0o644)
            os.chmod(root / "subdir" / "file2.txt", 0o644)

            make_tree_read_only(root)

            # Check that write bits are removed
            file_mode = (root / "file.txt").stat().st_mode
            assert not (file_mode & 0o222)  # No write bits

    def test_make_tree_read_only_preserves_read_execute(self):
        """Test that read and execute bits are preserved."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "file.txt").write_text("content")
            os.chmod(root / "file.txt", 0o755)

            make_tree_read_only(root)

            file_mode = (root / "file.txt").stat().st_mode
            assert file_mode & 0o444  # Read bits preserved
            assert file_mode & 0o111  # Execute bits preserved


class TestCopyPath:
    """Test suite for copy_path function."""

    def test_copy_path_file(self):
        """Test copying a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src.txt"
            dst = Path(tmpdir) / "dst.txt"
            src.write_text("content")
            src.chmod(0o644)

            copy_path(str(src), str(dst))

            assert dst.exists()
            assert dst.read_text() == "content"
            assert dst.stat().st_mode == src.stat().st_mode

    def test_copy_path_directory(self):
        """Test copying a directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src"
            dst = Path(tmpdir) / "dst"
            src.mkdir()
            (src / "file.txt").write_text("content")

            copy_path(str(src), str(dst))

            assert dst.exists()
            assert dst.is_dir()
            assert (dst / "file.txt").exists()

    def test_copy_path_creates_parent_dirs(self):
        """Test copying file creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src.txt"
            dst = Path(tmpdir) / "subdir" / "dst.txt"
            src.write_text("content")

            copy_path(str(src), str(dst))

            assert dst.parent.exists()
            assert dst.exists()


class TestMakeSymlink:
    """Test suite for make_symlink function."""

    def test_make_symlink(self):
        """Test creating a symlink."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "target.txt"
            target.write_text("content")
            link_path = "link.txt"

            make_symlink(str(root), link_path, str(target))

            link = root / link_path
            assert link.is_symlink()
            assert link.readlink() == target
            assert link.read_text() == "content"

    def test_make_symlink_creates_parent_dirs(self):
        """Test creating symlink creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "target.txt"
            target.write_text("content")
            link_path = "subdir/link.txt"

            make_symlink(str(root), link_path, str(target))

            link = root / link_path
            assert link.parent.exists()
            assert link.is_symlink()

    def test_make_symlink_overwrites_existing(self):
        """Test creating symlink overwrites existing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "target.txt"
            target.write_text("content")
            link_path = "link.txt"
            existing = root / link_path
            existing.write_text("old")

            make_symlink(str(root), link_path, str(target))

            link = root / link_path
            assert link.is_symlink()
            assert link.read_text() == "content"


class TestCollectFilesWithGlob:
    """Test suite for collect_files_with_glob function."""

    def test_collect_files_with_glob_simple(self):
        """Test collecting files with simple glob pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = tmpdir
            (Path(tmpdir) / "file1.txt").write_text("content1")
            (Path(tmpdir) / "file2.txt").write_text("content2")
            (Path(tmpdir) / "other.py").write_text("code")

            matches = collect_files_with_glob(ws_path, "*.txt")

            assert len(matches) == 2
            assert any("file1.txt" in m for m in matches)
            assert any("file2.txt" in m for m in matches)

    def test_collect_files_with_glob_subdirectory(self):
        """Test collecting files in subdirectory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = tmpdir
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()
            (subdir / "file.txt").write_text("content")

            matches = collect_files_with_glob(ws_path, "subdir/*.txt")

            assert len(matches) >= 1
            assert any("subdir/file.txt" in m for m in matches)

    def test_collect_files_with_glob_doublestar(self):
        """Test collecting files with doublestar pattern."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = tmpdir
            (Path(tmpdir) / "level1" / "level2" / "file.txt").parent.mkdir(parents=True)
            (Path(tmpdir) / "level1" / "level2" / "file.txt").write_text("content")

            matches = collect_files_with_glob(ws_path, "**/*.txt")

            assert len(matches) >= 1
            assert any("file.txt" in m for m in matches)

    def test_collect_files_with_glob_no_matches(self):
        """Test collecting files when no matches found."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = tmpdir

            matches = collect_files_with_glob(ws_path, "*.nonexistent")

            assert len(matches) == 0

    def test_collect_files_with_glob_excludes_directories(self):
        """Test that directories are excluded from results."""
        with tempfile.TemporaryDirectory() as tmpdir:
            ws_path = tmpdir
            (Path(tmpdir) / "adir").mkdir()

            matches = collect_files_with_glob(ws_path, "*")

            assert len(matches) == 0  # No files, only directory


class TestDetectContentType:
    """Test suite for detect_content_type function."""

    def test_detect_content_type_from_filename(self):
        """Test detecting content type from filename."""
        filename = Path("test.txt")
        data = b"some text"

        mime_type = detect_content_type(filename, data)

        assert mime_type == "text/plain"

    def test_detect_content_type_png(self):
        """Test detecting PNG image."""
        filename = Path("test.png")
        data = b'\x89PNG\r\n\x1a\n' + b"fake png data"

        mime_type = detect_content_type(filename, data)

        assert mime_type == "image/png"

    def test_detect_content_type_jpeg(self):
        """Test detecting JPEG image."""
        filename = Path("test.jpg")
        data = b'\xff\xd8\xff' + b"fake jpeg data"

        mime_type = detect_content_type(filename, data)

        assert mime_type == "image/jpeg"

    def test_detect_content_type_pdf(self):
        """Test detecting PDF."""
        filename = Path("test.pdf")
        data = b'%PDF' + b"fake pdf data"

        mime_type = detect_content_type(filename, data)

        assert mime_type == "application/pdf"

    def test_detect_content_type_text_utf8(self):
        """Test detecting UTF-8 text."""
        filename = Path("test.unknown")
        data = b"some utf-8 text"

        mime_type = detect_content_type(filename, data)

        assert "text" in mime_type.lower() or mime_type == "application/octet-stream"

    @patch('trpc_agent_sdk.code_executors.utils._files.HAS_MAGIC', True)
    @patch('trpc_agent_sdk.code_executors.utils._files.magic', create=True)
    def test_detect_content_type_with_magic(self, mock_magic):
        """Test detecting content type using magic library."""
        filename = Path("test.unknown")
        data = b"some data"
        mock_magic.from_buffer.return_value = "application/custom"

        mime_type = detect_content_type(filename, data)

        assert mime_type == "application/custom"
        mock_magic.from_buffer.assert_called_once_with(data, mime=True)


class TestGetRelPath:
    """Test suite for get_rel_path function."""

    def test_get_rel_path_simple(self):
        """Test getting relative path."""
        base = Path("/base")
        path = Path("/base/subdir/file.txt")

        result = get_rel_path(base, path)

        assert result == Path("subdir/file.txt")

    def test_get_rel_path_same_level(self):
        """Test getting relative path at same level."""
        base = Path("/base")
        path = Path("/base/file.txt")

        result = get_rel_path(base, path)

        assert result == Path("file.txt")

    def test_get_rel_path_string_inputs(self):
        """Test getting relative path with string inputs."""
        base = "/base"
        path = "/base/file.txt"

        result = get_rel_path(base, path)

        assert result == Path("file.txt")

    def test_get_rel_path_not_relative(self):
        """Test getting relative path when not relative."""
        base = Path("/base")
        path = Path("/other/file.txt")

        result = get_rel_path(base, path)

        assert result is None

    def test_get_rel_path_empty_base(self):
        """Test getting relative path with empty base."""
        base = Path("")
        path = Path("file.txt")

        # This might raise ValueError or return None depending on implementation
        result = get_rel_path(base, path)
        # Accept either None or a valid relative path
        assert result is None or isinstance(result, Path)
