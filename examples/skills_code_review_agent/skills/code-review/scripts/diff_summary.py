#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
diff_summary.py - Diff 摘要生成脚本（通过 skill_run 执行）

此脚本从 stdin 读取 git diff，生成变更摘要统计。
可通过 skill_load + skill_run 在隔离 workspace 中执行。
"""
import sys
import re


def parse_diff(diff_content: str) -> dict:
    """解析 diff 内容，提取统计信息"""
    lines = diff_content.split("\n")

    stats = {
        "files_changed": 0,
        "additions": 0,
        "deletions": 0,
        "files": [],
    }

    current_file = None

    for line in lines:
        if line.startswith("diff --git"):
            stats["files_changed"] += 1
            # 提取文件名
            match = re.search(r"b/(.+)$", line)
            if match:
                current_file = match.group(1)
                stats["files"].append(current_file)
        elif line.startswith("+") and not line.startswith("+++"):
            stats["additions"] += 1
        elif line.startswith("-") and not line.startswith("---"):
            stats["deletions"] += 1

    return stats


def main():
    """主函数：从 stdin 读取 diff，输出摘要"""
    diff_content = sys.stdin.read()
    stats = parse_diff(diff_content)

    # 输出摘要
    output = [
        f"文件变更: {stats['files_changed']}",
        f"新增行: {stats['additions']}",
        f"删除行: {stats['deletions']}",
        "",
        "变更文件列表:",
    ]
    for f in stats["files"]:
        output.append(f"  - {f}")

    print("\n".join(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
