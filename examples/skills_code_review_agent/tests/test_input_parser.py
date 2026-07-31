# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Business tests for unified diff normalization."""

from __future__ import annotations

from examples.skills_code_review_agent.agent import InputType
from examples.skills_code_review_agent.agent import parse_file_list
from examples.skills_code_review_agent.agent import parse_unified_diff


def test_parser_preserves_added_line_numbers():
    summary = parse_unified_diff(
        """diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -2,3 +2,4 @@ def run():
     value = 1
-    return value
+    value = value + 1
+    return value
""",
        task_id="task-1",
    )

    changed = summary.changed_files[0]
    assert summary.input_type is InputType.DIFF_FILE
    assert changed.path == "src/app.py"
    assert changed.candidate_lines == [3, 4]
    assert changed.hunks[0].lines[2].new_line == 3
    assert changed.hunks[0].lines[1].old_line == 3


def test_new_file_candidate_lines_are_added_lines_only():
    summary = parse_unified_diff(
        """diff --git a/new.py b/new.py
new file mode 100644
--- /dev/null
+++ b/new.py
@@ -0,0 +1,3 @@
+first
+
+third
""",
        task_id="task-2",
    )

    changed = summary.changed_files[0]
    assert changed.status == "added"
    assert changed.added_lines == 3
    assert changed.deleted_lines == 0
    assert changed.candidate_lines == [1, 2, 3]


def test_deleted_file_preserves_old_path():
    summary = parse_unified_diff(
        """diff --git a/removed.txt b/removed.txt
deleted file mode 100644
--- a/removed.txt
+++ /dev/null
@@ -1,2 +0,0 @@
-one
-two
""",
        task_id="task-3",
    )

    changed = summary.changed_files[0]
    assert changed.status == "deleted"
    assert changed.old_path == "removed.txt"
    assert changed.path == "removed.txt"
    assert changed.candidate_lines == []


def test_rename_preserves_old_and_new_paths():
    summary = parse_unified_diff(
        """diff --git a/old.py b/new.py
similarity index 88%
rename from old.py
rename to new.py
--- a/old.py
+++ b/new.py
@@ -1 +1 @@
-old_name()
+new_name()
""",
        task_id="task-4",
    )

    changed = summary.changed_files[0]
    assert changed.status == "renamed"
    assert changed.old_path == "old.py"
    assert changed.path == "new.py"
    assert changed.candidate_lines == [1]


def test_multi_file_multi_hunk_diff_is_counted():
    summary = parse_unified_diff(
        """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-a = 1
+a = 2
@@ -10 +10,2 @@ section
 keep = True
+extra = True
diff --git a/b.py b/b.py
--- a/b.py
+++ b/b.py
@@ -3 +3 @@
-b = 1
+b = 2
""",
        task_id="task-5",
    )

    assert summary.file_count == 2
    assert summary.hunk_count == 3
    assert summary.added_lines == 3
    assert summary.deleted_lines == 2
    assert summary.changed_files[0].hunks[1].section_header == "section"


def test_context_lines_are_preserved():
    summary = parse_unified_diff(
        """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
 def run():
-    return 1
+    return 2
 # tail
""",
        task_id="task-6",
    )

    lines = summary.changed_files[0].hunks[0].lines
    assert [line.line_type for line in lines] == ["context", "deleted", "added", "context"]
    assert lines[0].old_line == 1
    assert lines[0].new_line == 1


def test_binary_patch_records_diagnostic_without_failure():
    summary = parse_unified_diff(
        """diff --git a/image.bin b/image.bin
new file mode 100644
Binary files /dev/null and b/image.bin differ
""",
        task_id="task-7",
    )

    changed = summary.changed_files[0]
    assert changed.status == "added"
    assert changed.is_binary is True
    assert changed.hunks == []
    assert any("binary patch" in item for item in summary.diagnostics)


def test_malformed_hunk_keeps_parsed_content_and_diagnostics():
    summary = parse_unified_diff(
        """diff --git a/good.py b/good.py
--- a/good.py
+++ b/good.py
@@ -1 +1 @@
-old
+new
diff --git a/bad.py b/bad.py
--- a/bad.py
+++ b/bad.py
@@ broken
+lost
""",
        task_id="task-8",
    )

    assert summary.file_count == 2
    assert summary.changed_files[0].candidate_lines == [1]
    assert any("malformed hunk header" in item for item in summary.diagnostics)


def test_empty_diff_returns_stable_empty_summary():
    summary = parse_unified_diff("", task_id="task-9")

    assert summary.changed_files == []
    assert summary.file_count == 0
    assert summary.raw_diff_sha256 == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_same_diff_has_same_sha256_digest():
    diff = """diff --git a/a.py b/a.py
--- a/a.py
+++ b/a.py
@@ -1 +1 @@
-a
+b
"""

    first = parse_unified_diff(diff, task_id="task-a")
    second = parse_unified_diff(diff, task_id="task-b")

    assert first.raw_diff_sha256 == second.raw_diff_sha256


def test_file_list_input_builds_added_file_summary(tmp_path):
    source = tmp_path / "app.py"
    source.write_text("print('hello')\n", encoding="utf-8")
    file_list = tmp_path / "files.txt"
    file_list.write_text("# comment\napp.py\nmissing.py\n", encoding="utf-8")

    summary = parse_file_list(file_list, task_id="task-file-list")

    assert summary.input_type is InputType.FILE_LIST
    assert summary.file_count == 1
    assert summary.changed_files[0].status == "added"
    assert summary.changed_files[0].candidate_lines == [1]
    assert any("missing.py" in item for item in summary.diagnostics)
