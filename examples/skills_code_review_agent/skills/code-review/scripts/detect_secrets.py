#!/usr/bin/env python3
"""Detect sensitive information in a file.

Usage:
    python detect_secrets.py <file> <output_file>

Output:
    JSON file with a list of detected secrets (type, location, content preview)
"""

import json
import re
import sys
from pathlib import Path
from typing import Any


# Secret detection patterns
SECRET_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r'(?i)(?:api_key|api[_-]?key|apikey)\s*[=:]\s*[\'"](sk-[a-zA-Z0-9]{10,})[\'"]'),
     "API Key", "critical"),
    (re.compile(r'(?i)(?:password|passwd|pwd)\s*[=:]\s*[\'"][^\'"]{4,}[\'"]'),
     "Password", "critical"),
    (re.compile(r"ghp_[a-zA-Z0-9]{36,}"),
     "GitHub Token", "critical"),
    (re.compile(r"AKIA[0-9A-Z]{16}"),
     "AWS Access Key", "critical"),
    (re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
     "Private Key", "critical"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
     "JWT Token", "critical"),
    (re.compile(r"(?:postgres(?:ql)?|mysql|redis)://[^:]+:[^@]+@"),
     "DB Connection String", "critical"),
    (re.compile(r'(?i)(?:token|secret|credential)\s*[=:]\s*[\'"][^\'"]{8,}[\'"]'),
     "Generic Secret", "warning"),
]


def detect_secrets(file_path: str) -> list[dict[str, Any]]:
    """Detect secrets in a file."""
    findings: list[dict[str, Any]] = []
    content = Path(file_path).read_text(encoding="utf-8") if Path(file_path).exists() else ""

    for line_no, line in enumerate(content.splitlines(), 1):
        for pattern, label, severity in SECRET_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue

            evidence = match.group()
            if len(evidence) > 60:
                evidence = evidence[:57] + "..."

            findings.append({
                "severity": severity,
                "category": "secret",
                "file": str(file_path),
                "line": line_no,
                "title": f"检测到{label}",
                "evidence": evidence,
                "recommendation": "移除硬编码的敏感信息，使用环境变量或密钥管理服务",
                "confidence": "high",
                "source": "static_check",
            })

    return findings


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python detect_secrets.py <file> <output_file>", file=sys.stderr)
        sys.exit(1)

    file_path = sys.argv[1]
    output_file = sys.argv[2]

    findings = detect_secrets(file_path)
    Path(output_file).parent.mkdir(parents=True, exist_ok=True)
    Path(output_file).write_text(
        json.dumps({"secrets": findings}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Secret detection complete: {len(findings)} secrets found")