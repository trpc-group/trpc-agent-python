#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
static_review.py - 静态代码审查脚本（通过 skill_run 执行）

此脚本从 stdin 读取 git diff，执行基础静态分析，输出 JSON 格式报告。
可通过 skill_load + skill_run 在隔离 workspace 中执行。
"""
import json
import re
import sys
from typing import List, Dict


# 基础安全规则（简化版，演示用）
SECURITY_PATTERNS = [
    (r"sk-[A-Za-z0-9]{20,}", "stripe_api_key", "硬编码 Stripe API 密钥"),
    (r"ghp_[A-Za-z0-9]{36}", "github_token", "硬编码 GitHub 个人访问令牌"),
    (r"AKIA[0-9A-Z]{16}", "aws_access_key", "硬编码 AWS 访问密钥 ID"),
    (r"password\s*=\s*['\"][^'\"]+['\"]", "hardcoded_password", "硬编码密码"),
    (r"api_key\s*=\s*['\"][^'\"]+['\"]", "hardcoded_api_key", "硬编码 API 密钥"),
]


def check_line_for_secrets(line: str, file_path: str, line_num: int) -> List[Dict]:
    """检查单行代码是否包含敏感信息"""
    findings = []
    for pattern, rule_id, description in SECURITY_PATTERNS:
        if re.search(pattern, line, re.IGNORECASE):
            findings.append({
                "rule_id": rule_id,
                "severity": "critical",
                "category": "security",
                "file": file_path,
                "line": line_num,
                "title": description,
                "evidence": line.strip(),
                "recommendation": "使用环境变量或配置管理服务存储敏感信息"
            })
    return findings


def parse_diff_line(line: str) -> tuple:
    """解析 diff 行，返回 (file_path, line_num, content)"""
    if line.startswith("+++ b/"):
        return (line[6:], None, None)  # 新文件路径
    if line.startswith("@@"):
        # 提取新增行的起始号，格式：@@ -old_start,old_count +new_start,new_count @@
        match = re.search(r"\+(\d+)", line)
        if match:
            return (None, int(match.group(1)), None)  # 新行号
    if line.startswith("+") and not line.startswith("+++"):
        return (None, None, line[1:])  # 新增内容
    return (None, None, None)


def main():
    """主函数：从 stdin 读取 diff，输出 JSON 报告"""
    diff_content = sys.stdin.read()

    findings = []
    current_file = None
    current_line = None

    for line in diff_content.split("\n"):
        file_path, line_num, content = parse_diff_line(line)

        if file_path:
            current_file = file_path
        elif line_num is not None:
            current_line = line_num
        elif content and current_file and current_line is not None:
            # 检查新增行是否包含敏感信息
            line_findings = check_line_for_secrets(content, current_file, current_line)
            findings.extend(line_findings)
            current_line += 1

    # 输出 JSON 报告
    report = {
        "status": "completed",
        "findings_count": len(findings),
        "findings": findings,
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
