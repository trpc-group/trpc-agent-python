# tests/test_diff_parser.py
import pytest
from agent.diff_parser import parse_diff, parse_file_list, parse_git_worktree


def test_parse_unified_diff_extracts_added_lines():
    """测试解析unified diff格式并提取新增行"""
    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 def f():
-    return 1
+    return 2
+    api_key = "sk-secret123"
"""
    files = parse_diff(diff)
    assert len(files) == 1
    assert files[0].path == "app.py"
    added = [line.content for line in files[0].added_lines]
    assert any("sk-secret123" in c for c in added)  # 原文保留给规则检测；脱敏在落库层


def test_parse_diff_multiple_files():
    """测试解析多文件diff"""
    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,4 @@
 def f():
-    return 1
+    return 2
diff --git a/utils.py b/utils.py
--- a/utils.py
+++ b/utils.py
@@ -1,2 +1,3 @@
 def helper():
-    return "old"
+    return "new"
+    secret = "key"
"""
    files = parse_diff(diff)
    assert len(files) == 2
    assert files[0].path == "app.py"
    assert files[1].path == "utils.py"
    assert len(files[0].added_lines) == 1
    assert len(files[1].added_lines) == 2


def test_parse_diff_hunk_structure():
    """测试hunk结构解析正确性"""
    diff = """diff --git a/test.py b/test.py
--- a/test.py
+++ b/test.py
@@ -5,3 +5,4 @@
 def old_func():
     pass
+def new_func():
+    pass
"""
    files = parse_diff(diff)
    assert len(files) == 1
    assert len(files[0].hunks) == 1
    hunk = files[0].hunks[0]
    assert hunk.old_start == 5
    assert hunk.new_start == 5
    assert len(hunk.added) == 2
    # hunk从第5行开始，前两行是上下文（5,6），新增行从第7行开始
    assert hunk.added[0].new_line == 7
    assert hunk.added[1].new_line == 8


def test_parse_diff_context_after():
    """测试context_after收集（包括+和空行）"""
    diff = """diff --git a/example.py b/example.py
--- a/example.py
+++ b/example.py
@@ -1,2 +1,4 @@
 # comment
-    old_line
+    new_line
+    another_new
     context
"""
    files = parse_diff(diff)
    assert len(files) == 1
    hunk = files[0].hunks[0]
    # context_after应该包含hunk内所有的+行和空行（上下文行）
    assert len(hunk.context_after) > 0
    # 检查新增行在context_after中
    assert any("new_line" in line for line in hunk.context_after)


def test_parse_file_list():
    """测试从文件列表构造DiffFile"""
    import tempfile
    import os

    # 创建临时文件用于测试
    with tempfile.TemporaryDirectory() as tmpdir:
        file1 = os.path.join(tmpdir, "test1.py")
        file2 = os.path.join(tmpdir, "test2.py")

        with open(file1, 'w') as f:
            f.write("def func1():\n    pass\n")
        with open(file2, 'w') as f:
            f.write("def func2():\n    pass\n")

        files = parse_file_list([file1, file2])
        assert len(files) == 2
        assert files[0].path == file1
        assert files[1].path == file2
        assert len(files[0].hunks) == 1  # 单个hunk包含整个文件


def test_parse_diff_deleted_lines():
    """测试删除行的解析"""
    diff = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,2 @@
 def f():
-    old_line
     return 1
"""
    files = parse_diff(diff)
    assert len(files) == 1
    # 删除的行不应出现在added_lines中
    assert len(files[0].added_lines) == 0
    # 但hunk应该被正确解析
    assert len(files[0].hunks) == 1


def test_parse_diff_empty():
    """测试空diff输入"""
    files = parse_diff("")
    assert len(files) == 0


def test_parse_git_worktree():
    """测试git工作区解析（需要git仓库）"""
    import tempfile
    import subprocess
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        # 初始化git仓库
        subprocess.run(["git", "init"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmpdir, capture_output=True)

        # 创建初始文件并commit
        test_file = os.path.join(tmpdir, "test.py")
        with open(test_file, 'w') as f:
            f.write("old content\n")
        subprocess.run(["git", "add", "."], cwd=tmpdir, capture_output=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=tmpdir, capture_output=True)

        # 修改文件
        with open(test_file, 'w') as f:
            f.write("new content\n")

        # 测试parse_git_worktree
        files = parse_git_worktree(tmpdir)
        assert len(files) == 1
        path_condition = (files[0].path == "test.py" or "test.py" in files[0].path)
        assert path_condition


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
