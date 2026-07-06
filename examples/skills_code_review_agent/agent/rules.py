"""Deterministic static rules for issue #92 phase 1."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable

from .diff_parser import ChangedLine
from .findings import Finding
from .redaction import redact_text


_SECRET_NAME_RE = re.compile(
    r"\b(api[_-]?(?:key|token)|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd)\b",
    re.I,
)
_SECRET_ASSIGN_RE = re.compile(
    r"\b(?:api[_-]?(?:key|token)|access[_-]?token|auth[_-]?token|token|secret|password|passwd|pwd)\b"
    r"\s*[:=]\s*[\"'][^\"']{8,}[\"']",
    re.I,
)
_SECRET_LITERAL_RE = re.compile(r"\bsk-[A-Za-z0-9]{12,}\b")
_SQL_WORD_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|REPLACE|DROP|ALTER)\b", re.I)
_HTTP_CALL_RE = re.compile(r"\b(?:requests|httpx)\.(?:get|post|put|patch|delete|request|stream)\s*\(")
_BROAD_EXCEPT_RE = re.compile(r"^\s*except\s+Exception(?:\s+as\s+\w+)?\s*:")
_OPEN_CALL_RE = re.compile(r"\bopen\s*\(")


def _finding(
    *,
    line: ChangedLine,
    severity: str,
    category: str,
    title: str,
    recommendation: str,
    confidence: float,
    source: str,
) -> Finding:
    return Finding(
        severity=severity,
        category=category,
        file=line.file_path,
        line=line.line_number,
        title=title,
        evidence=redact_text(line.content.strip()),
        recommendation=recommendation,
        confidence=confidence,
        source=source,
    )


def _added_lines(changed_lines: Iterable[ChangedLine]) -> list[ChangedLine]:
    return [line for line in changed_lines if line.change_type == "add"]


def _line_map(lines: list[ChangedLine]) -> dict[str, list[ChangedLine]]:
    grouped: dict[str, list[ChangedLine]] = defaultdict(list)
    for line in lines:
        grouped[line.file_path].append(line)
    for file_lines in grouped.values():
        file_lines.sort(key=lambda item: item.line_number)
    return grouped


def _looks_like_secret(line: str) -> bool:
    if "os.environ" in line or "getenv(" in line:
        return False
    return bool(_SECRET_ASSIGN_RE.search(line) or _SECRET_LITERAL_RE.search(line))


def _looks_like_sql_concat(line: str) -> bool:
    if not _SQL_WORD_RE.search(line):
        return False
    compact = line.replace(" ", "")
    return any(
        marker in line or marker in compact
        for marker in (
            " + ",
            "+",
            "f\"",
            "f'",
            ".format(",
            "% ",
            "%(",
        )
    )


def _looks_like_missing_timeout(line: str) -> bool:
    if not _HTTP_CALL_RE.search(line):
        return False
    if ")" not in line:
        return False
    return "timeout=" not in line


def _looks_like_resource_lifecycle(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("with "):
        return False
    if "Path(" in line and ".open(" in line:
        return not stripped.startswith("with ")
    return bool(_OPEN_CALL_RE.search(line))


def _next_added_line(file_lines: list[ChangedLine], index: int) -> ChangedLine | None:
    if index + 1 >= len(file_lines):
        return None
    return file_lines[index + 1]


def run_static_rules(changed_lines: Iterable[ChangedLine]) -> list[Finding]:
    """Run all deterministic phase-1 rules over added diff lines."""

    additions = _added_lines(changed_lines)
    findings: list[Finding] = []

    for line in additions:
        content = line.content

        if _looks_like_secret(content):
            title = "Possible hardcoded secret"
            if _SECRET_NAME_RE.search(content):
                title = "Possible hardcoded secret, token, or password"
            findings.append(
                _finding(
                    line=line,
                    severity="high",
                    category="secret",
                    title=title,
                    recommendation="Move secrets to a secret manager or environment variable and rotate exposed values.",
                    confidence=0.9,
                    source="static-rule:hardcoded-secret",
                )
            )

        if _looks_like_sql_concat(content):
            findings.append(
                _finding(
                    line=line,
                    severity="high",
                    category="sql-injection",
                    title="SQL query appears to be built with string interpolation or concatenation",
                    recommendation="Use parameterized queries or the database driver's bind parameter API.",
                    confidence=0.78,
                    source="static-rule:sql-string-concat",
                )
            )

        if _looks_like_missing_timeout(content):
            findings.append(
                _finding(
                    line=line,
                    severity="medium",
                    category="network-timeout",
                    title="HTTP request is missing an explicit timeout",
                    recommendation="Pass a bounded timeout, for example timeout=10, to avoid hanging workers.",
                    confidence=0.86,
                    source="static-rule:http-timeout",
                )
            )

        if _looks_like_resource_lifecycle(content):
            findings.append(
                _finding(
                    line=line,
                    severity="medium",
                    category="resource-lifecycle",
                    title="File handle may not be closed on all paths",
                    recommendation="Use a context manager such as with open(...) as f to guarantee cleanup.",
                    confidence=0.72,
                    source="static-rule:open-without-context-manager",
                )
            )

    for file_lines in _line_map(additions).values():
        for index, line in enumerate(file_lines):
            if not _BROAD_EXCEPT_RE.search(line.content):
                continue
            next_line = _next_added_line(file_lines, index)
            swallowed = bool(next_line and next_line.content.strip() in {"pass", "return None", "return False"})
            findings.append(
                _finding(
                    line=line,
                    severity="high" if swallowed else "medium",
                    category="error-handling",
                    title="Broad exception handler may hide failures",
                    recommendation="Catch the narrowest expected exception and log or re-raise unexpected failures.",
                    confidence=0.82 if swallowed else 0.74,
                    source="static-rule:broad-except",
                )
            )

    return sorted(findings, key=lambda item: (item.file, item.line, item.category, item.source))
