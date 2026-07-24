---
name: code-review
description: 代码审查技能，执行静态代码分析和安全规则检查。
---

概述

代码审查技能在隔离的工作空间中执行静态代码分析和安全规则检查。
支持 Python 代码的 AST 分析、正则规则检测和敏感信息扫描。

使用示例

1) 执行静态代码审查（从 stdin 读取 diff）

   命令：

   python3 scripts/static_review.py < inputs/diff.txt > out/report.json

2) 生成 diff 摘要

   命令：

   python3 scripts/diff_summary.py < inputs/diff.txt > out/summary.txt

输入文件

- inputs/diff.txt: Git unified diff 格式的代码变更

输出文件

- out/report.json: JSON 格式的审查报告，包含 findings、warnings 等
- out/summary.txt: Diff 变更摘要
