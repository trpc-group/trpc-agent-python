# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
diff_summary.py - 从标准输入读取 diff 并输出变更摘要

这是 code-review skill 的约定脚本之一，被 Task 12 pipeline 调用。
从 stdin 读取 git diff 输出，生成结构化的变更摘要。
"""

import sys
import json
from pathlib import Path
from typing import Dict


def parse_diff_line(line: str) -> Dict[str, str]:
    """解析单行 diff，判断文件变更类型"""
    line = line.strip()
    if not line:
        return {}

    # Git diff 格式: 文件路径前缀标识变更类型
    if line.startswith("diff --git"):
        # 提取文件名
        parts = line.split()
        if len(parts) >= 4:
            return {"type": "header", "path": parts[3][2:]}  # 去掉 "b/" 前缀
    elif line.startswith("new file"):
        return {"type": "new", "path": line.split()[-1]}
    elif line.startswith("deleted file"):
        return {"type": "deleted", "path": line.split()[-1]}
    elif line.startswith("index"):
        return {"type": "index", "hash": line.split()[1]}
    elif line.startswith("---"):
        return {"type": "old_file", "path": line.split()[1][2:]}  # 去掉 "a/" 前缀
    elif line.startswith("+++"):
        return {"type": "new_file", "path": line.split()[1][2:]}  # 去掉 "b/" 前缀
    elif line.startswith("@@"):
        # 提取行号范围
        return {"type": "hunk", "range": line.split()[1:-1]}
    elif line.startswith("+"):
        return {"type": "addition", "content": line[1:]}
    elif line.startswith("-"):
        return {"type": "deletion", "content": line[1:]}

    return {}


def analyze_diff(diff_content: str) -> Dict:
    """分析 diff 内容，生成变更摘要"""
    lines = diff_content.split("\n")

    summary = {
        "files": {
            "added": [],
            "modified": [],
            "deleted": []
        },
        "statistics": {
            "additions": 0,
            "deletions": 0,
            "total_files": 0
        },
        "modules": [],
        "errors": []
    }

    current_file = None
    file_state = None  # "new", "modified", "deleted"

    for line in lines:
        parsed = parse_diff_line(line)

        if parsed.get("type") == "header":
            # 新文件开始
            current_file = parsed.get("path", "")
            file_state = "modified"
        elif parsed.get("type") == "new":
            file_state = "new"
        elif parsed.get("type") == "deleted":
            file_state = "deleted"

        # 统计文件变更
        if parsed.get("type") in ("new_file", "old_file") and current_file:
            if file_state == "new" and parsed.get("type") == "new_file":
                summary["files"]["added"].append(parsed.get("path", ""))
                summary["statistics"]["total_files"] += 1
            elif file_state == "deleted" and parsed.get("type") == "old_file":
                summary["files"]["deleted"].append(parsed.get("path", ""))
                summary["statistics"]["total_files"] += 1
            elif file_state == "modified":
                # modified 文件同时有 old_file 和 new_file
                if parsed.get("type") == "new_file" and current_file not in summary["files"]["modified"]:
                    summary["files"]["modified"].append(parsed.get("path", ""))
                    summary["statistics"]["total_files"] += 1

        # 统计增删行数
        if parsed.get("type") == "addition":
            summary["statistics"]["additions"] += 1
        elif parsed.get("type") == "deletion":
            summary["statistics"]["deletions"] += 1

    # 识别主要变更模块（基于文件路径）
    all_files = (summary["files"]["added"] + summary["files"]["modified"] + summary["files"]["deleted"])

    module_set = set()
    for file_path in all_files:
        # 简单的模块识别：取前两级目录
        parts = Path(file_path).parts
        if len(parts) >= 2:
            module_set.add("/".join(parts[:2]))
        elif len(parts) == 1:
            module_set.add(parts[0])

    summary["modules"] = sorted(list(module_set))

    return summary


def main():
    """主函数：从 stdin 读取 diff 并输出摘要"""
    try:
        # 读取 stdin
        diff_content = sys.stdin.read()

        if not diff_content.strip():
            # 没有输入，返回空摘要
            empty_summary = {
                "files": {
                    "added": [],
                    "modified": [],
                    "deleted": []
                },
                "statistics": {
                    "additions": 0,
                    "deletions": 0,
                    "total_files": 0
                },
                "modules": [],
                "errors": ["No diff input provided"]
            }
            print(json.dumps(empty_summary, indent=2, ensure_ascii=False))
            return

        # 分析 diff
        summary = analyze_diff(diff_content)

        # 输出 JSON 格式的摘要
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    except Exception as e:
        # 错误处理
        error_summary = {
            "files": {
                "added": [],
                "modified": [],
                "deleted": []
            },
            "statistics": {
                "additions": 0,
                "deletions": 0,
                "total_files": 0
            },
            "modules": [],
            "errors": [f"Error analyzing diff: {str(e)}"]
        }
        print(json.dumps(error_summary, indent=2, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
