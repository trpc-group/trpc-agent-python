#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#

import hashlib
import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = PROJECT_ROOT / "skills" / "code-review" / "scripts"
sys.path.insert(0, str(SCRIPTS_ROOT))

from lib.diff_parser import build_snapshot_change_set, parse_unified_diff  # noqa: E402


def test_parse_modified_diff_exposes_changed_lines_and_context_mapping() -> None:
    diff_text = (
        "diff --git a/src/app.py b/src/app.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/src/app.py\n"
        "+++ b/src/app.py\n"
        "@@ -10,4 +10,4 @@\n"
        " context_before\n"
        "-old_value\n"
        "+new_value\n"
        " context_after\n"
        " context_last\n"
    )

    change_set = parse_unified_diff(diff_text, source_kind="diff_file")

    assert change_set.source_kind == "diff_file"
    assert change_set.input_sha256 == hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    assert change_set.file_count == 1
    assert change_set.hunk_count == 1
    assert change_set.additions == 1
    assert change_set.deletions == 1
    assert change_set.parse_warnings == ()

    file_change = change_set.files[0]
    assert file_change.old_path == "src/app.py"
    assert file_change.new_path == "src/app.py"
    assert file_change.normalized_path == "src/app.py"
    assert file_change.status == "modified"
    assert file_change.review_scope == "changed_lines"
    assert file_change.analysis_mode == "diff_heuristic"
    assert file_change.old_changed_lines == (11,)
    assert file_change.new_changed_lines == (11,)
    assert file_change.full_text is None

    hunk = file_change.hunks[0]
    assert (hunk.old_start, hunk.old_count) == (10, 4)
    assert (hunk.new_start, hunk.new_count) == (10, 4)
    assert hunk.context_lines == {
        10: "context_before",
        12: "context_after",
        13: "context_last",
    }
    assert hunk.deleted_lines == {11: "old_value"}
    assert hunk.added_lines == {11: "new_value"}
    assert hunk.old_to_new_line_map == {10: 10, 12: 12, 13: 13}


def test_parse_rename_normalizes_quoted_paths_with_spaces() -> None:
    diff_text = (
        'diff --git "a/src/old name.py" "b/src/new name.py"\n'
        "similarity index 100%\n"
        "rename from src/old name.py\n"
        "rename to src/new name.py\n"
    )

    file_change = parse_unified_diff(diff_text).files[0]

    assert file_change.old_path == "src/old name.py"
    assert file_change.new_path == "src/new name.py"
    assert file_change.normalized_path == "src/new name.py"
    assert file_change.status == "renamed"
    assert file_change.review_scope == "changed_lines"
    assert file_change.hunks == ()


def test_parse_binary_diff_marks_file_as_skipped() -> None:
    diff_text = (
        "diff --git a/assets/logo.png b/assets/logo.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/assets/logo.png and b/assets/logo.png differ\n"
    )

    file_change = parse_unified_diff(diff_text).files[0]

    assert file_change.status == "modified"
    assert file_change.is_binary is True
    assert file_change.review_scope == "skipped"
    assert file_change.analysis_mode == "skipped"
    assert file_change.hunks == ()


def test_parse_complete_added_file_handles_crlf_and_no_newline_marker() -> None:
    diff_text = (
        "diff --git a/src/new.py b/src/new.py\r\n"
        "new file mode 100644\r\n"
        "--- /dev/null\r\n"
        "+++ b/src/new.py\r\n"
        "@@ -0,0 +1,2 @@\r\n"
        "+first = 1\r\n"
        "+second = 2\r\n"
        "\\ No newline at end of file\r\n"
    )

    change_set = parse_unified_diff(diff_text)
    file_change = change_set.files[0]
    hunk = file_change.hunks[0]

    assert change_set.input_sha256 == hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    assert file_change.old_path == "/dev/null"
    assert file_change.new_path == "src/new.py"
    assert file_change.normalized_path == "src/new.py"
    assert file_change.status == "added"
    assert file_change.review_scope == "full_file"
    assert file_change.old_changed_lines == ()
    assert file_change.new_changed_lines == (1, 2)
    assert file_change.full_text == "first = 1\nsecond = 2"
    assert file_change.analysis_mode == "ast_validated"
    assert (hunk.old_start, hunk.old_count) == (0, 0)
    assert (hunk.new_start, hunk.new_count) == (1, 2)
    assert hunk.old_to_new_line_map == {}


def test_parse_deleted_file_preserves_real_old_line_numbers() -> None:
    diff_text = (
        "diff --git a/src/obsolete.py b/src/obsolete.py\n"
        "deleted file mode 100644\n"
        "--- a/src/obsolete.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-first = 1\n"
        "-second = 2\n"
    )

    file_change = parse_unified_diff(diff_text).files[0]
    hunk = file_change.hunks[0]

    assert file_change.old_path == "src/obsolete.py"
    assert file_change.new_path == "/dev/null"
    assert file_change.normalized_path == "src/obsolete.py"
    assert file_change.status == "deleted"
    assert file_change.review_scope == "deleted_lines"
    assert file_change.old_changed_lines == (1, 2)
    assert file_change.new_changed_lines == ()
    assert file_change.full_text is None
    assert (hunk.old_start, hunk.old_count) == (1, 2)
    assert (hunk.new_start, hunk.new_count) == (0, 0)
    assert hunk.old_to_new_line_map == {}


def test_build_snapshot_change_set_uses_full_file_scope_and_zero_old_side() -> None:
    change_set = build_snapshot_change_set(
        {r".\src\snapshot.py": "first = 1\nsecond = 2\n"},
        source_kind="files",
    )

    file_change = change_set.files[0]
    hunk = file_change.hunks[0]

    assert change_set.source_kind == "files"
    assert len(change_set.input_sha256) == 64
    assert change_set.additions == 2
    assert change_set.deletions == 0
    assert file_change.old_path == "/dev/null"
    assert file_change.new_path == "src/snapshot.py"
    assert file_change.normalized_path == "src/snapshot.py"
    assert file_change.status == "snapshot"
    assert file_change.review_scope == "full_file"
    assert file_change.old_changed_lines == ()
    assert file_change.new_changed_lines == (1, 2)
    assert file_change.full_text == "first = 1\nsecond = 2\n"
    assert file_change.analysis_mode == "ast_validated"
    assert (hunk.old_start, hunk.old_count) == (0, 0)
    assert (hunk.new_start, hunk.new_count) == (1, 2)
    assert hunk.context_lines == {}
    assert hunk.deleted_lines == {}
    assert hunk.added_lines == {1: "first = 1", 2: "second = 2"}
    assert hunk.old_to_new_line_map == {}


def test_parse_plain_unified_diff_without_git_metadata() -> None:
    diff_text = (
        "--- src/tool.py\t2026-07-24 10:00:00\n"
        "+++ src/tool.py\t2026-07-25 10:00:00\n"
        "@@ -2,2 +2,2 @@\n"
        "-old_value = 1\n"
        "+new_value = 2\n"
        " unchanged = True\n"
    )

    file_change = parse_unified_diff(diff_text).files[0]

    assert file_change.status == "modified"
    assert file_change.normalized_path == "src/tool.py"
    assert file_change.old_changed_lines == (2,)
    assert file_change.new_changed_lines == (2,)
    assert file_change.hunks[0].old_to_new_line_map == {3: 3}


def test_parse_no_newline_markers_on_both_sides_of_replacement() -> None:
    diff_text = (
        "diff --git a/value.txt b/value.txt\n"
        "--- a/value.txt\n"
        "+++ b/value.txt\n"
        "@@ -1 +1 @@\n"
        "-old value\n"
        "\\ No newline at end of file\n"
        "+new value\n"
        "\\ No newline at end of file\n"
    )

    hunk = parse_unified_diff(diff_text).files[0].hunks[0]

    assert hunk.deleted_lines == {1: "old value"}
    assert hunk.added_lines == {1: "new value"}
    assert hunk.old_to_new_line_map == {}


def test_empty_added_and_deleted_files_do_not_create_fake_hunks() -> None:
    diff_text = (
        "diff --git a/empty_added.py b/empty_added.py\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/empty_added.py\n"
        "diff --git a/empty_deleted.py b/empty_deleted.py\n"
        "deleted file mode 100644\n"
        "--- a/empty_deleted.py\n"
        "+++ /dev/null\n"
    )

    added, deleted = parse_unified_diff(diff_text).files

    assert added.status == "added"
    assert added.review_scope == "full_file"
    assert added.hunks == ()
    assert added.full_text == ""
    assert added.old_changed_lines == ()
    assert added.new_changed_lines == ()
    assert deleted.status == "deleted"
    assert deleted.review_scope == "deleted_lines"
    assert deleted.hunks == ()
    assert deleted.full_text is None
    assert deleted.old_changed_lines == ()
    assert deleted.new_changed_lines == ()


def test_snapshot_hash_and_file_order_are_deterministic() -> None:
    first = build_snapshot_change_set(
        {
            "b/module.py": "b = 2\n",
            "a/module.py": "a = 1\n",
        }
    )
    reordered = build_snapshot_change_set(
        {
            "a/module.py": "a = 1\n",
            "b/module.py": "b = 2\n",
        }
    )
    changed = build_snapshot_change_set(
        {
            "a/module.py": "a = 3\n",
            "b/module.py": "b = 2\n",
        }
    )

    assert first.input_sha256 == reordered.input_sha256
    assert first.input_sha256 != changed.input_sha256
    assert [file.normalized_path for file in first.files] == [
        "a/module.py",
        "b/module.py",
    ]


def test_binary_new_file_uses_mode_metadata_for_added_status() -> None:
    diff_text = (
        "diff --git a/assets/new.bin b/assets/new.bin\n"
        "new file mode 100644\n"
        "index 0000000..1111111\n"
        "GIT binary patch\n"
        "literal 1\n"
        "AcmZQz\n"
    )

    file_change = parse_unified_diff(diff_text).files[0]

    assert file_change.status == "added"
    assert file_change.normalized_path == "assets/new.bin"
    assert file_change.is_binary is True
    assert file_change.review_scope == "skipped"
    assert file_change.analysis_mode == "skipped"


@pytest.mark.parametrize(
    "unsafe_path",
    (
        "../outside.py",
        "/absolute.py",
        "C:/absolute.py",
        "src/../../outside.py",
        "src/control\u0007.py",
    ),
)
def test_diff_parser_rejects_paths_outside_the_review_namespace(
    unsafe_path: str,
) -> None:
    """验证绝对路径、父级穿越和控制字符不会进入 ChangeSet。"""

    diff_text = (
        f"diff --git a/{unsafe_path} b/{unsafe_path}\n"
        f"--- a/{unsafe_path}\n"
        f"+++ b/{unsafe_path}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    with pytest.raises(ValueError, match="path"):
        parse_unified_diff(diff_text)
