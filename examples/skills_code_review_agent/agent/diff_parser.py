# agent/diff_parser.py - 输入解析层：unified diff/文件列表/git工作区 → DiffFile/Hunk/ChangedLine
import subprocess
import os
import re
from agent.models import DiffFile, Hunk, ChangedLine


def parse_diff(diff_text: str) -> list[DiffFile]:
    """解析unified diff格式文本，返回DiffFile列表

    Args:
        diff_text: unified diff格式的文本

    Returns:
        DiffFile对象列表
    """
    if not diff_text or not diff_text.strip():
        return []

    files = []
    current_file = None
    current_hunk = None
    new_line_counter = 0
    old_line_counter = 0

    for line in diff_text.split('\n'):
        line = line.rstrip('\n')

        # 识别文件开始：diff --git a/path b/path
        if line.startswith('diff --git '):
            parts = line.split()
            if len(parts) >= 4:
                # 提取路径（去掉 a/ 和 b/ 前缀）
                file_path = parts[2][2:] if parts[2].startswith('a/') else parts[2]
                current_file = DiffFile(path=file_path, status='modified')
                files.append(current_file)
                current_hunk = None
            continue

        # 识别旧文件路径：--- a/path 或 --- /dev/null
        if line.startswith('--- '):
            if current_file and '/dev/null' in line:
                # 旧文件为 /dev/null 表示新文件
                current_file.status = 'added'
            continue

        # 识别新文件路径：+++ b/path 或 +++ /dev/null
        if line.startswith('+++ '):
            if current_file and '/dev/null' in line:
                # 新文件为 /dev/null 表示删除文件
                current_file.status = 'deleted'
            continue

        # 识别hunk头：@@ -a,b +c,d @@
        if line.startswith('@@') and ' +' in line:
            if not current_file:
                continue

            # 解析hunk头：@@ -old_start,old_count +new_start,new_count @@
            try:
                # 提取 -old_start,old_count 和 +new_start,new_count
                hunk_match = re.search(r'@@\s*-(\d+),?\d*\s*\+(\d+),?\d*\s*@@', line)
                if hunk_match:
                    old_start = int(hunk_match.group(1))
                    new_start = int(hunk_match.group(2))

                    current_hunk = Hunk(file=current_file.path, old_start=old_start, new_start=new_start)
                    current_file.hunks.append(current_hunk)

                    # 重置行计数器
                    old_line_counter = old_start
                    new_line_counter = new_start
            except (ValueError, IndexError):
                pass
            continue

        # 处理hunk内容行
        if current_hunk and current_file:
            # 新增行 +
            if line.startswith('+') and not line.startswith('+++'):
                content = line[1:]  # 去掉 + 前缀
                changed_line = ChangedLine(file=current_file.path,
                                           new_line=new_line_counter,
                                           old_line=None,
                                           content=content)
                current_file.added_lines.append(changed_line)
                current_hunk.added.append(changed_line)
                current_hunk.context_after.append(content)
                new_line_counter += 1

            # 删除行 -
            elif line.startswith('-') and not line.startswith('---'):
                # 删除行不计入 added_lines，但需要更新行计数器
                old_line_counter += 1

            # 上下文行（空格开头）
            elif line.startswith(' '):
                content = line[1:]  # 去掉空格前缀
                current_hunk.context_after.append(content)
                new_line_counter += 1
                old_line_counter += 1

    return files


def parse_file_list(paths: list[str]) -> list[DiffFile]:
    """从文件路径列表构造DiffFile列表（读取文件内容构造单hunk）

    Args:
        paths: 文件路径列表

    Returns:
        DiffFile对象列表
    """
    files = []

    for path in paths:
        if not os.path.exists(path):
            continue

        # 读取文件内容
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            # 读取失败时跳过该文件（文件不存在、权限问题或编码错误）
            continue

        # 构造DiffFile，将整个文件作为一个hunk
        diff_file = DiffFile(path=path, status='modified')

        # 按行分割内容
        lines = content.split('\n')

        # 创建单个hunk包含整个文件
        hunk = Hunk(file=path, old_start=1, new_start=1)

        # 每行都作为新增行处理
        for line_num, line_content in enumerate(lines, start=1):
            changed_line = ChangedLine(file=path, new_line=line_num, old_line=None, content=line_content)
            diff_file.added_lines.append(changed_line)
            hunk.added.append(changed_line)
            hunk.context_after.append(line_content)

        diff_file.hunks.append(hunk)
        files.append(diff_file)

    return files


def parse_git_worktree(repo_path: str) -> list[DiffFile]:
    """通过git diff获取工作区变更并解析

    Args:
        repo_path: git仓库路径

    Returns:
        DiffFile对象列表
    """
    try:
        # 执行 git diff HEAD 获取工作区变更
        result = subprocess.run(["git", "diff", "HEAD"], cwd=repo_path, capture_output=True, text=True, check=False)

        if result.returncode != 0:
            return []

        # 获取diff文本
        diff_text = result.stdout

        # 解析diff
        return parse_diff(diff_text)

    except (OSError, subprocess.SubprocessError):
        # git命令执行失败时返回空列表
        return []
