#!/usr/bin/env python3
"""Detect database connections, sessions, and transactions without cleanup."""

import re

from review_common import ParsedDiff
from review_common import added_changes
from review_common import current_text
from review_common import file_path
from review_common import finding
from review_common import run_rule_cli

RULE_NAME = "database_lifecycle"


def _managed(name: str, text: str, methods: str) -> bool:
    escaped = re.escape(name)
    return bool(
        re.search(rf"\b{escaped}\.(?:{methods})\s*\(", text, re.IGNORECASE)
        or re.search(rf"\brelease\s*\(\s*{escaped}\s*\)", text, re.IGNORECASE)
    )


def review(parsed: ParsedDiff) -> list[dict[str, object]]:
    """Return deterministic database lifecycle candidates."""
    findings = []
    constructors = (
        (
            re.compile(
                r"^\s*(\w+)\s*=\s*(?:(?:sqlite3|psycopg2|pymysql)\.)?connect\s*\("
                r"|^\s*(\w+)\s*=\s*\w+\.(?:connect|acquire)\s*\(",
                re.IGNORECASE,
            ),
            "close|aclose|release",
            "Database connection",
        ),
        (
            re.compile(
                r"^\s*(\w+)\s*=\s*(?:sessionmaker\([^)]*\)|"
                r"(?:async)?session(?:local)?\s*\()",
                re.IGNORECASE,
            ),
            "close|aclose",
            "Database session",
        ),
        (
            re.compile(r"^\s*(\w+)\s*=\s*\w+\.cursor\s*\(", re.IGNORECASE),
            "close|aclose",
            "Database cursor",
        ),
        (
            re.compile(r"^\s*(\w+)\s*=\s*\w+\.begin\s*\(", re.IGNORECASE),
            "commit|rollback|close",
            "Database transaction",
        ),
    )
    for file_data in parsed.get("files", []):
        path = file_path(file_data)
        text = current_text(file_data)
        for _hunk, change in added_changes(file_data):
            content = str(change.get("content", ""))
            if re.match(r"^\s*(?:async\s+)?with\b", content, re.IGNORECASE):
                continue
            for constructor, cleanup, handle_name in constructors:
                match = constructor.search(content)
                if not match:
                    continue
                name = next(group for group in match.groups() if group)
                if _managed(name, text, cleanup):
                    break
                findings.append(
                    finding(
                        severity="high",
                        category=RULE_NAME,
                        file=path,
                        line=change.get("new_line"),
                        title=f"{handle_name} may outlive its intended lifecycle",
                        evidence=content,
                        recommendation=(
                            "Use a managed lifecycle and guarantee rollback/release/close "
                            "on success and exception paths."
                        ),
                        confidence=0.90,
                        source="skill:review_database.py",
                    )
                )
                break
    return findings


if __name__ == "__main__":
    raise SystemExit(run_rule_cli(RULE_NAME, review))
