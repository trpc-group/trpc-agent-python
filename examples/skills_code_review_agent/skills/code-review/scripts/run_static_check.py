#!/usr/bin/env python3
"""Run static analysis on a file and output findings.

Usage:
    python run_static_check.py <file> <rules_dir> <output_file>

Output:
    JSON file with a list of findings (severity, category, file, line, title, etc.)
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


# Pattern definitions
PATTERNS: list[dict[str, Any]] = [
    # Security: SQL injection
    {
        "severity": "critical", "category": "security",
        "pattern": re.compile(r"cursor\.execute\(f\s*['\"]"),
        "title": "SQL注入风险",
        "recommendation": "使用参数化查询",
    },
    # Security: Command injection
    {
        "severity": "critical", "category": "security",
        "pattern": re.compile(r"os\.system\(f\s*['\"]"),
        "title": "命令注入风险",
        "recommendation": "使用 subprocess.run() 传递列表参数",
    },
    # Security: shell=True
    {
        "severity": "critical", "category": "security",
        "pattern": re.compile(r"subprocess\.(?:call|Popen|run)\(.*shell=True"),
        "title": "Shell注入风险",
        "recommendation": "禁用 shell=True",
    },
    # Security: eval/exec
    {
        "severity": "warning", "category": "security",
        "pattern": re.compile(r"eval\(|exec\("),
        "title": "动态代码执行",
        "recommendation": "避免使用 eval/exec",
    },
    # Resource: file handle not closed
    {
        "severity": "warning", "category": "resource_leak",
        "pattern": re.compile(r"open\([^)]+\)(?!\s*as\s)"),
        "title": "文件句柄未使用 context manager",
        "recommendation": "使用 with open() as f:",
    },
    # Async: blocking call
    {
        "severity": "warning", "category": "async",
        "pattern": re.compile(r"time\.sleep\("),
        "title": "阻塞调用在异步代码中",
        "recommendation": "使用 asyncio.sleep()",
    },
    # DB: connection not closed
    {
        "severity": "warning", "category": "db",
        "pattern": re.compile(r"sqlite3\.connect\(.*\)(?!.*\.close\()"),
        "title": "数据库连接未关闭",
        "recommendation": "使用 with 语句管理连接",
    },
    # Maintainability: TODO/FIXME
    {
        "severity": "suggestion", "category": "maintainability",
        "pattern": re.compile(r"(?i)(TODO|FIXME|HACK|XXX)\b"),
        "title": "遗留标记",
        "recommendation": "在提交前解决 TODO/FIXME",
    },
]


def run_static_check(file_path: str, rules_dir: str) -> list[dict[str, Any]]:
    """Run static analysis on a file."""
    findings: list[dict[str, Any]] = []
    content = Path(file_path).read_text(encoding="utf-8") if Path(file_path).exists() else ""

    for line_no, line in enumerate(content.splitlines(), 1):
        for pat in PATTERNS:
            match = pat["pattern"].search(line)
            if not match:
                continue

            findings.append({
                "severity": pat["severity"],
                "category": pat["category"],
                "file": str(file_path),
                "line": line_no,
                "title": pat["title"],
                "evidence": line.strip()[:80],
                "recommendation": pat["recommendation"],
                "confidence": "high" if pat["severity"] == "critical" else "medium",
                "source": "static_check",
            })

    return findings


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: python run_static_check.py <file> <rules_dir> <output_file>", file=sys.stderr)
        sys.exit(1)

    file_path = sys.argv[1]
    rules_dir = sys.argv[2]
    output_file = sys.argv[3]

    findings = run_static_check(file_path, rules_dir)
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    Path(output_file).write_text(
        json.dumps({"findings": findings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Static check complete: {len(findings)} findings")