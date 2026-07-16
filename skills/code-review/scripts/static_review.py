# -*- coding: utf-8 -*-
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""
static_review.py - 静态代码检查脚本

这是 code-review skill 的约定脚本之一，对代码文件执行静态分析。
支持基于 Python AST 的语法检查和基于正则的模式匹配。
可以独立运行，或调用宿主 agent 的 rule_engine。
"""

import ast
import os
import re
import sys
import json
from typing import Dict, List


class StaticReviewer:
    """静态代码审查器"""

    def __init__(self):
        self.issues = []
        self.rules = self._load_rules()

    def _load_rules(self) -> Dict[str, List[Dict]]:
        """加载检查规则（简化版正则规则）"""
        return {
            "security": [{
                "name": "SQL 注入风险",
                "pattern": r'(execute|executemany|query)\s*\(\s*["\'][^"\']*%s',
                "severity": "high",
                "description": "可能存在 SQL 注入风险，使用参数化查询"
            }, {
                "name": "硬编码密钥",
                "pattern": r'(API_KEY|SECRET|PASSWORD|TOKEN)\s*=\s*["\'][\w-]+["\']',
                "severity": "critical",
                "description": "检测到硬编码的密钥或密码，应使用环境变量"
            }, {
                "name": "不安全的随机数",
                "pattern": r'random\.(choices|choice|randint|shuffle)\s*\(',
                "severity": "medium",
                "description": "安全相关场景应使用 secrets 模块"
            }],
            "async_errors": [{
                "name": "过于宽泛的异常捕获",
                "pattern": r'except\s*:\s*$',
                "severity": "medium",
                "description": "避免裸 except，应捕获特定异常"
            }, {
                "name": "吞掉异常",
                "pattern": r'except\s+\w+\s*:\s*pass\s*$',
                "severity": "low",
                "description": "异常捕获后应至少记录日志"
            }],
            "resource_leak": [{
                "name": "文件未使用 with 语句",
                "pattern": r'\bopen\s*\(\s*["\'][^"\']+["\']\s*\)\s*(?!\s+as\s+)',
                "severity": "medium",
                "description": "文件操作应使用 with 语句确保关闭"
            }]
        }

    def review_file(self, file_path: str, content: str = None) -> List[Dict]:
        """审查单个文件"""
        if content is None:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                return [{
                    "file": file_path,
                    "line": 0,
                    "rule": "file_read_error",
                    "severity": "error",
                    "message": f"无法读取文件: {e}"
                }]

        issues = []

        # 1. AST 检查（Python 文件）
        if file_path.endswith('.py'):
            try:
                ast_issues = self._check_ast(file_path, content)
                issues.extend(ast_issues)
            except SyntaxError as e:
                issues.append({
                    "file": file_path,
                    "line": e.lineno or 0,
                    "rule": "syntax_error",
                    "severity": "error",
                    "message": f"语法错误: {e.msg}"
                })

        # 2. 正则规则检查
        regex_issues = self._check_regex_rules(file_path, content)
        issues.extend(regex_issues)

        return issues

    def _check_ast(self, file_path: str, content: str) -> List[Dict]:
        """基于 AST 的检查"""
        issues = []
        try:
            tree = ast.parse(content, filename=file_path)

            for node in ast.walk(tree):
                # 检查未处理的资源
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        if node.func.id == 'open':
                            # 检查是否在 with 语句中
                            if not self._is_in_with_statement(node):
                                issues.append({
                                    "file": file_path,
                                    "line": node.lineno,
                                    "rule": "resource_leak",
                                    "severity": "medium",
                                    "message": "open() 应使用 with 语句确保文件关闭"
                                })

                # 检查过于宽泛的异常处理
                if isinstance(node, ast.ExceptHandler):
                    if node.type is None:
                        issues.append({
                            "file": file_path,
                            "line": node.lineno,
                            "rule": "broad_exception",
                            "severity": "medium",
                            "message": "避免使用裸 except，应捕获特定异常类型"
                        })

        except Exception as e:
            issues.append({
                "file": file_path,
                "line": 0,
                "rule": "ast_parse_error",
                "severity": "error",
                "message": f"AST 解析错误: {e}"
            })

        return issues

    def _is_in_with_statement(self, node: ast.AST) -> bool:
        """检查节点是否在 with 语句中"""
        # 简化版本：实际需要更复杂的父节点遍历
        # 这里只做基本检查
        return False

    def _check_regex_rules(self, file_path: str, content: str) -> List[Dict]:
        """基于正则规则的检查"""
        issues = []
        lines = content.split('\n')

        for category, rules in self.rules.items():
            for rule in rules:
                pattern = re.compile(rule['pattern'])
                for line_no, line in enumerate(lines, 1):
                    if pattern.search(line):
                        issues.append({
                            "file": file_path,
                            "line": line_no,
                            "rule": rule['name'],
                            "severity": rule['severity'],
                            "category": category,
                            "message": rule['description'],
                            "code_snippet": line.strip()
                        })

        return issues

    def review_files(self, file_paths: List[str]) -> Dict:
        """审查多个文件"""
        all_issues = []

        for file_path in file_paths:
            if not os.path.exists(file_path):
                all_issues.append({
                    "file": file_path,
                    "line": 0,
                    "rule": "file_not_found",
                    "severity": "error",
                    "message": f"文件不存在: {file_path}"
                })
                continue

            issues = self.review_file(file_path)
            all_issues.extend(issues)

        # 按严重程度和文件分组
        result = {
            "summary": {
                "total_issues": len(all_issues),
                "by_severity": {},
                "by_category": {},
                "files_reviewed": len(file_paths)
            },
            "issues": all_issues
        }

        # 统计严重程度分布
        for issue in all_issues:
            severity = issue.get("severity", "unknown")
            result["summary"]["by_severity"][severity] = \
                result["summary"]["by_severity"].get(severity, 0) + 1

            category = issue.get("category", "unknown")
            result["summary"]["by_category"][category] = \
                result["summary"]["by_category"].get(category, 0) + 1

        return result


def main():
    """主函数"""
    if len(sys.argv) < 2:
        # 没有提供文件参数，从 stdin 读取文件列表
        file_list_input = sys.stdin.read().strip().split('\n')
        file_paths = [f.strip() for f in file_list_input if f.strip()]
    else:
        # 从命令行参数获取文件列表
        file_paths = sys.argv[1:]

    if not file_paths:
        empty_result = {
            "summary": {
                "total_issues": 0,
                "by_severity": {},
                "by_category": {},
                "files_reviewed": 0
            },
            "issues": [],
            "errors": ["没有提供要审查的文件"]
        }
        print(json.dumps(empty_result, indent=2, ensure_ascii=False))
        return

    # 执行静态审查
    reviewer = StaticReviewer()
    result = reviewer.review_files(file_paths)

    # 输出结果
    print(json.dumps(result, indent=2, ensure_ascii=False))

    # 如果有严重问题，返回非零退出码
    critical_count = result["summary"]["by_severity"].get("critical", 0)
    error_count = result["summary"]["by_severity"].get("error", 0)
    if critical_count > 0 or error_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
