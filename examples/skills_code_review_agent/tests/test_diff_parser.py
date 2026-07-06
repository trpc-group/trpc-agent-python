# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Input parsing (issue requirement 3): diff text, file lists, git workspaces."""

import os
import shutil
import subprocess

import pytest

from codereview.diff_parser import build_diff_summary
from codereview.diff_parser import parse_unified_diff
from codereview.inputs import from_file_list
from codereview.inputs import from_repo_path

MULTI_FILE_DIFF = """\
diff --git a/pkg/alpha.py b/pkg/alpha.py
index 000001..000002 100644
--- a/pkg/alpha.py
+++ b/pkg/alpha.py
@@ -10,7 +10,8 @@ def existing():
 context_line_1
-removed_line
+added_line_one
+added_line_two
 context_line_2
@@ -30,3 +31,4 @@ def later():
 tail_context
+tail_added
 more_context
diff --git a/pkg/new_module.py b/pkg/new_module.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/pkg/new_module.py
@@ -0,0 +1,2 @@
+first = 1
+second = 2
diff --git a/pkg/gone.py b/pkg/gone.py
deleted file mode 100644
index 2222222..0000000
--- a/pkg/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-old_one
-old_two
diff --git a/pkg/old_name.py b/pkg/renamed.py
similarity index 90%
rename from pkg/old_name.py
rename to pkg/renamed.py
index 3333333..4444444 100644
--- a/pkg/old_name.py
+++ b/pkg/renamed.py
@@ -1,2 +1,3 @@
 keep
+renamed_added
 keep_too
"""


def test_multi_file_parse():
    changeset = parse_unified_diff(MULTI_FILE_DIFF)
    files = {entry["path"]: entry for entry in changeset["files"]}
    assert set(files) == {"pkg/alpha.py", "pkg/new_module.py", "pkg/gone.py", "pkg/renamed.py"}
    assert files["pkg/alpha.py"]["status"] == "modified"
    assert files["pkg/new_module.py"]["status"] == "added"
    assert files["pkg/gone.py"]["status"] == "deleted"
    assert files["pkg/renamed.py"]["status"] == "renamed"
    assert files["pkg/renamed.py"]["old_path"] == "pkg/old_name.py"


def test_hunk_line_numbering_and_context():
    changeset = parse_unified_diff(MULTI_FILE_DIFF)
    alpha = next(entry for entry in changeset["files"] if entry["path"] == "pkg/alpha.py")
    assert len(alpha["hunks"]) == 2

    first = alpha["hunks"][0]
    assert first["old_start"] == 10 and first["new_start"] == 10
    tags = [line["tag"] for line in first["lines"]]
    assert tags == [" ", "-", "+", "+", " "]
    # candidate (added) line numbers must be exact new-file line numbers
    assert [line["line"] for line in alpha["added_lines"]] == [11, 12, 32]
    assert [line["content"] for line in alpha["added_lines"]] == [
        "added_line_one", "added_line_two", "tail_added",
    ]
    # removed lines carry old-file numbers
    assert alpha["removed_lines"][0]["line"] == 11
    # context lines keep both counters aligned
    context = first["lines"][-1]
    assert context["tag"] == " " and context["old_lineno"] == 12 and context["new_lineno"] == 13


def test_plain_diff_without_git_header():
    plain = ("--- a/tool.py\n"
             "+++ b/tool.py\n"
             "@@ -1,2 +1,3 @@\n"
             " import sys\n"
             "+import os\n"
             " print(1)\n")
    changeset = parse_unified_diff(plain)
    assert len(changeset["files"]) == 1
    assert changeset["files"][0]["path"] == "tool.py"
    assert changeset["files"][0]["added_lines"] == [{"line": 2, "content": "import os"}]


def test_binary_file_flagged():
    diff = ("diff --git a/logo.png b/logo.png\n"
            "index 1111111..2222222 100644\n"
            "Binary files a/logo.png and b/logo.png differ\n")
    changeset = parse_unified_diff(diff)
    assert changeset["files"][0]["is_binary"] is True


def test_diff_summary_is_content_free():
    changeset = parse_unified_diff(MULTI_FILE_DIFF)
    summary = build_diff_summary(changeset)
    assert summary["file_count"] == 4
    assert summary["added_line_count"] == 6
    assert summary["removed_line_count"] == 3
    alpha = next(entry for entry in summary["files"] if entry["path"] == "pkg/alpha.py")
    assert alpha["candidate_lines"] == [11, 12, 32]
    # No raw line content may leak into the summary (it is stored in the DB).
    assert "added_line_one" not in str(summary)


def test_file_list_input(tmp_path):
    target = tmp_path / "svc.py"
    target.write_text("import os\nos.system('ls')\n", encoding="utf-8")
    changeset = from_file_list([str(target)], base_dir=str(tmp_path))
    parsed = parse_unified_diff(changeset.unified_diff_text)
    assert parsed["files"][0]["path"] == "svc.py"
    assert parsed["files"][0]["status"] == "added"
    assert changeset.file_contents["svc.py"].startswith("import os")


@pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")
def test_git_repo_input(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}

    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], check=True, env=env,
                       capture_output=True)

    git("init", "-q")
    (repo / "main.py").write_text("print('v1')\n", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "init")
    # tracked modification + untracked new file
    (repo / "main.py").write_text("print('v2')\nvalue = 3\n", encoding="utf-8")
    (repo / "extra.py").write_text("extra = True\n", encoding="utf-8")

    changeset = from_repo_path(str(repo))
    parsed = parse_unified_diff(changeset.unified_diff_text)
    paths = {entry["path"] for entry in parsed["files"]}
    assert paths == {"main.py", "extra.py"}
    assert changeset.file_contents["main.py"].startswith("print('v2')")
    assert changeset.file_contents["extra.py"] == "extra = True\n"
