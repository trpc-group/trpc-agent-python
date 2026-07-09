#!/usr/bin/env python3
"""Run code review scanners against a file or diff.

Usage: python run_checks.py <filename>
       python run_checks.py <filename> --scanners security,async_errors
       python run_checks.py <filename> --format json
"""

import argparse
import json
import os
import re
import sys
from typing import Any


# ── Scanner definitions (mirrors pipeline/scanners.py patterns) ────────

_PATTERNS: list[dict[str, Any]] = [
    # Security
    {
        "category": "security",
        "patterns": [
            (re.compile(r"\bos\.system\s*\([^)]*\+"), "Shell command concatenation — possible injection"),
            (re.compile(r"\bsubprocess\.(?:call|run|Popen)\s*\(\s*shell\s*=\s*True"),
             "subprocess with shell=True — command injection risk"),
            (re.compile(r"\beval\s*\([^)]*\+"), "eval() with string concatenation — code injection risk"),
            (re.compile(r"\bexec\s*\([^)]*\+"), "exec() with string concatenation — code injection risk"),
            (re.compile(r"\bpickle\.load"), "Unsafe pickle deserialization"),
            (re.compile(r"\b__import__\s*\([^)]*\+"), "Dynamic import with concatenation"),
        ],
    },
    # Async errors
    {
        "category": "async_error",
        "patterns": [
            (re.compile(r"\btime\.sleep\s*\("), "time.sleep() in async context blocks event loop"),
            (re.compile(r"\bdef\s+\w+\([^)]*\):\s*\n(?:[^\n]*\n)*?\s+await\s+",
                        re.MULTILINE),  # simplified
            (re.compile(r"\bfor\s+\w+\s+in\s+(?!range\b)"),
             "Potential synchronous iteration in async context"),
        ],
    },
    # Resource leaks
    {
        "category": "resource_leak",
        "patterns": [
            (re.compile(r"\bopen\s*\([^)]*\)(?!.*\bclose\b)"), "open() without explicit close() — resource leak"),
            (re.compile(r"(?<!with\s.*)\bopen\s*\([^)]*\)(?!.*\bas\b)"),
             "open() outside context manager"),
        ],
    },
    # DB lifecycle
    {
        "category": "db_lifecycle",
        "patterns": [
            (re.compile(r"\.execute\s*\([^)]*\)(?!.*\bcommit\b)"), "execute() without commit()"),
            (re.compile(r"\.cursor\s*\([^)]*\)(?!.*\bclose\b)"), "cursor() without close()"),
            (re.compile(r"\.connect\s*\([^)]*\)(?!.*\bclose\b)"), "connect() without close()"),
        ],
    },
    # Missing tests
    {
        "category": "missing_tests",
        "patterns": [
            (re.compile(r"^\+.*def\s+(?!test_)(\w+)\s*\([^)]*\):", re.MULTILINE),
             "New function without 'test_' prefix — may lack test coverage"),
        ],
    },
    # Secret info
    {
        "category": "secret_info",
        "patterns": [
            (re.compile(r"(?:api_?key|API_?KEY|apikey)\s*=\s*['\"][^'\"]+['\"]"),
             "Hardcoded API key"),
            (re.compile(r"(?:password|PASSWORD|passwd)\s*=\s*['\"][^'\"]+['\"]"),
             "Hardcoded password"),
            (re.compile(r"(?:token|TOKEN|secret|SECRET)\s*=\s*['\"][^'\"]{8,}['\"]"),
             "Hardcoded token/secret"),
            (re.compile(r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{36,}"),
             "GitHub personal access token pattern"),
            (re.compile(r"AKIA[0-9A-Z]{16}"),
             "AWS access key pattern"),
        ],
    },
]


def run_checks(filename: str, scanners: list[str] | None = None) -> list[dict]:
    """Run scanners against a file and return structured findings.

    Args:
        filename: Path to the file to scan.
        scanners: Optional list of scanner categories to run. If None, run all.

    Returns:
        List of finding dicts with: category, title, severity, line, confidence.
    """
    try:
        with open(filename, "r", encoding="utf-8") as f:
            content = f.read()
    except FileNotFoundError:
        return [{"error": f"File not found: {filename}"}]
    except UnicodeDecodeError:
        return [{"error": f"Cannot decode file as UTF-8: {filename}"}]

    lines = content.split("\n")
    findings: list[dict] = []
    enabled = set(scanners) if scanners else None

    for group in _PATTERNS:
        if enabled and group["category"] not in enabled:
            continue

        for pattern, title in group["patterns"]:
            for i, line in enumerate(lines, start=1):
                if pattern.search(line):
                    findings.append({
                        "category": group["category"],
                        "title": title,
                        "file": filename,
                        "line": i,
                        "severity": "medium",
                        "confidence": 0.7,
                        "evidence": line.strip()[:120],
                    })

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run code review scanners against a file",
    )
    parser.add_argument("filename", help="File to scan")
    parser.add_argument("--scanners", help="Comma-separated scanner categories")
    parser.add_argument("--format", choices=["text", "json"], default="text",
                        help="Output format")
    args = parser.parse_args()

    scanner_list = None
    if args.scanners:
        scanner_list = [s.strip() for s in args.scanners.split(",")]

    try:
        findings = run_checks(args.filename, scanner_list)
    except Exception as e:
        print(f"Error running checks: {e}", file=sys.stderr)
        return 1

    if args.format == "json":
        json.dump(findings, sys.stdout, indent=2, ensure_ascii=False)
        return 0

    if not findings:
        print(f"Running checks on: {args.filename}")
        print("No issues found.")
        return 0

    # Error findings (file access issues, etc.)
    error_findings = [f for f in findings if "error" in f]
    real_findings = [f for f in findings if "error" not in f]

    if error_findings:
        for f in error_findings:
            print(f"Error: {f['error']}", file=sys.stderr)
        return 1

    print(f"Running checks on: {args.filename}")
    print(f"File: {args.filename}, Findings: {len(real_findings)}")
    for f in real_findings:
        print(f"  [{f['category']}] {f['file']}:{f['line']} — {f['title']}")
    print("Checks complete.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
