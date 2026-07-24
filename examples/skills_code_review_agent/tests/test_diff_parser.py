"""Tests for diff_parser module."""

from pipeline.diff_parser import get_changed_lines, parse_diff, summarize_diff
from pipeline.types import DiffFile


class TestParseDiff:
    """Unit tests for diff parsing."""

    def test_standard_unified_diff(self, read_diff):
        """Parse a standard unified diff with hunk headers."""
        diff_text = read_diff("security.diff")
        files = parse_diff(diff_text)
        assert len(files) == 1
        assert files[0].filename == "handler.py"
        assert len(files[0].hunks) == 1
        assert "+++ b/handler.py" in diff_text

    def test_multi_file_diff(self):
        """Parse a diff with multiple files."""
        diff = ("diff --git a/a.py b/a.py\n"
                "--- a/a.py\n+++ b/a.py\n"
                "@@ -1,0 +1,2 @@\n+def foo():\n+    pass\n"
                "diff --git a/b.py b/b.py\n"
                "--- a/b.py\n+++ b/b.py\n"
                "@@ -1,0 +1,2 @@\n+def bar():\n+    pass\n")
        files = parse_diff(diff)
        assert len(files) == 2
        assert files[0].filename == "a.py"
        assert files[1].filename == "b.py"

    def test_empty_diff(self):
        """Empty diff returns empty list."""
        assert parse_diff("") == []
        assert parse_diff("   \n  \n") == []

    def test_new_file_diff(self):
        """Parse a 'new file mode' diff."""
        diff = ("diff --git a/new.py b/new.py\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/new.py\n"
                "@@ -1,0 +1,1 @@\n+print('hello')\n")
        files = parse_diff(diff)
        assert len(files) == 1
        assert files[0].is_new
        assert files[0].filename == "new.py"

    def test_deleted_file_diff(self):
        """Parse a 'deleted file mode' diff."""
        diff = ("diff --git a/old.py b/old.py\n"
                "deleted file mode 100644\n"
                "--- a/old.py\n"
                "+++ /dev/null\n"
                "@@ -1,1 +1,0 @@\n-print('bye')\n")
        files = parse_diff(diff)
        assert len(files) == 1
        assert files[0].is_deleted

    def test_binary_file_diff(self):
        """Binary file diffs are marked as binary."""
        diff = ("diff --git a/image.png b/image.png\n"
                "Binary files a/image.png and b/image.png differ\n")
        files = parse_diff(diff)
        assert len(files) == 1
        assert files[0].is_binary

    def test_hunk_new_lines_tracking(self, read_diff):
        """Added lines should be extracted with correct line numbers."""
        diff_text = read_diff("security.diff")
        files = parse_diff(diff_text)
        changed = get_changed_lines(files[0])
        # Should have several added lines
        assert len(changed) > 0
        # All returned lines should have content
        for lineno, line in changed:
            assert lineno > 0
            assert isinstance(line, str)

    def test_unicode_diff(self):
        """Handle Unicode in file paths and content."""
        diff = ('diff --git "a/测试.py" "b/测试.py"\n'
                '--- "a/测试.py"\n+++ "b/测试.py"\n'
                '@@ -1,0 +1,1 @@\n+print("你好世界")\n')
        files = parse_diff(diff)
        assert len(files) == 1
        assert "测试.py" in files[0].filename


class TestSummarizeDiff:
    """Unit tests for diff summarization."""

    def test_empty(self):
        assert "No changes" in summarize_diff([])

    def test_single_file(self, read_diff):
        files = parse_diff(read_diff("security.diff"))
        summary = summarize_diff(files)
        assert "1 file" in summary
        assert "handler.py" in summary

    def test_multi_files(self):
        files = [
            DiffFile(filename="a.py"),
            DiffFile(filename="b.py"),
            DiffFile(filename="c.py"),
        ]
        summary = summarize_diff(files)
        assert "3 file" in summary
