# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic code review rules for the dry-run example."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .schemas import ChangedLine
from .schemas import ChangedLineKind
from .schemas import Confidence
from .schemas import FindingSource
from .schemas import ParsedDiff
from .schemas import ReviewFinding
from .schemas import Severity

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|apikey|token|secret|password)\b\s*[:=]\s*(['\"])(?P<value>[^'\"]{8,})\2"
)
_HEADER_SECRET_RE = re.compile(r"(?i)\b(authorization|cookie)\b\s*[:=]\s*(['\"])?(?P<value>[^'\"]{8,})")
_TOKEN_PREFIX_RE = re.compile(r"\b(sk|ghp|gho|xox[baprs])[-_][A-Za-z0-9_\-]{8,}\b")
_DB_URL_RE = re.compile(r"(?i)(postgres|mysql|redis|mongodb)://[^\s'\"]+:[^\s'\"]+@")


def review_with_rules(parsed_diff: ParsedDiff, *, include_missing_tests: bool = False) -> list[ReviewFinding]:
    """Run deterministic rules over added lines and diff-level context."""
    findings: list[ReviewFinding] = []
    changed_files = {diff_file.new_path or diff_file.old_path or "" for diff_file in parsed_diff.files}

    for diff_file in parsed_diff.files:
        if diff_file.is_binary or not diff_file.new_path:
            continue
        for hunk in diff_file.hunks:
            added_lines = [line for line in hunk.changed_lines if line.kind == ChangedLineKind.ADDED]
            for line in added_lines:
                if line.new_line_number is None:
                    continue
                findings.extend(_review_added_line(diff_file.new_path, line, added_lines))

    if include_missing_tests:
        findings.extend(_review_missing_tests(parsed_diff, changed_files))
    return findings


def _review_added_line(file_path: str, line: ChangedLine, hunk_added_lines: Iterable[ChangedLine]) -> list[ReviewFinding]:
    text = line.text.strip()
    line_no = line.new_line_number or 1
    findings: list[ReviewFinding] = []

    if _looks_like_secret(text):
        findings.append(
            _finding(
                severity=Severity.HIGH,
                category="secrets",
                file=file_path,
                line=line_no,
                title="Hard-coded secret in changed code",
                evidence=f"Added line contains a secret-like assignment: {line.text}",
                recommendation=(
                    "Move the secret to an environment variable or secret manager, "
                    "remove it from source control, and rotate the exposed value."
                ),
                confidence=Confidence.HIGH,
            )
        )

    if re.search(r"\b(eval|exec)\s*\(", text):
        findings.append(
            _finding(
                severity=Severity.HIGH,
                category="security",
                file=file_path,
                line=line_no,
                title="Dynamic code execution in changed code",
                evidence=f"Added line executes dynamic code: {line.text}",
                recommendation="Avoid eval/exec on untrusted data; use explicit parsing or dispatch tables instead.",
                confidence=Confidence.HIGH,
            )
        )

    if "shell=True" in text and "subprocess" in text:
        findings.append(
            _finding(
                severity=Severity.HIGH,
                category="security",
                file=file_path,
                line=line_no,
                title="Shell execution enabled for subprocess",
                evidence=f"Added line enables shell execution: {line.text}",
                recommendation="Pass arguments as a list with shell=False and validate any user-controlled input.",
                confidence=Confidence.HIGH,
            )
        )

    if re.search(r"(?i)(select|insert|update|delete).*(%\s|\.format\(|f['\"])", text):
        findings.append(
            _finding(
                severity=Severity.MEDIUM,
                category="security",
                file=file_path,
                line=line_no,
                title="SQL query appears to use string interpolation",
                evidence=f"Added SQL line may interpolate values directly: {line.text}",
                recommendation="Use parameterized queries instead of formatting values into SQL strings.",
                confidence=Confidence.MEDIUM,
            )
        )

    if "asyncio.create_task(" in text and "=" not in text and "await " not in text:
        findings.append(
            _finding(
                severity=Severity.MEDIUM,
                category="async",
                file=file_path,
                line=line_no,
                title="Created async task is not tracked",
                evidence=f"Added line creates an untracked task: {line.text}",
                recommendation="Store the task, await it, or attach cancellation/error handling so exceptions are observed.",
                confidence=Confidence.HIGH,
            )
        )

    if "aiohttp.ClientSession(" in text and "async with" not in text:
        findings.append(
            _finding(
                severity=Severity.MEDIUM,
                category="resource_leak",
                file=file_path,
                line=line_no,
                title="Async client session may not be closed",
                evidence=f"Added line creates a ClientSession without async context management: {line.text}",
                recommendation="Use `async with aiohttp.ClientSession()` or ensure the session is closed in a finally block.",
                confidence=Confidence.HIGH,
            )
        )

    if re.search(r"(?<!with\s)open\s*\(", text):
        findings.append(
            _finding(
                severity=Severity.MEDIUM,
                category="resource_leak",
                file=file_path,
                line=line_no,
                title="File handle may not be closed",
                evidence=f"Added line opens a file without a context manager: {line.text}",
                recommendation="Use `with open(...) as f:` or otherwise guarantee close() in a finally block.",
                confidence=Confidence.HIGH,
            )
        )

    if "NamedTemporaryFile" in text and "delete=False" in text and not _hunk_mentions_cleanup(hunk_added_lines):
        findings.append(
            _finding(
                severity=Severity.MEDIUM,
                category="resource_leak",
                file=file_path,
                line=line_no,
                title="Temporary file cleanup is not evident",
                evidence=f"Added line creates a persistent temporary file: {line.text}",
                recommendation="Ensure the temporary file is deleted after use, preferably in a finally block.",
                confidence=Confidence.MEDIUM,
            )
        )

    if _looks_like_db_connection(text) and not _line_has_lifecycle_guard(text) and not _hunk_mentions_db_cleanup(hunk_added_lines):
        findings.append(
            _finding(
                severity=Severity.MEDIUM,
                category="database_lifecycle",
                file=file_path,
                line=line_no,
                title="Database connection lifecycle is not guarded",
                evidence=f"Added line opens database/session resources without obvious cleanup: {line.text}",
                recommendation="Use a context manager or ensure commit/rollback and close are handled in finally blocks.",
                confidence=Confidence.HIGH,
            )
        )

    return findings


def _review_missing_tests(parsed_diff: ParsedDiff, changed_files: set[str]) -> list[ReviewFinding]:
    production_files = [
        diff_file.new_path
        for diff_file in parsed_diff.files
        if diff_file.new_path
        and diff_file.new_path.endswith(".py")
        and not diff_file.new_path.startswith("tests/")
        and "/tests/" not in diff_file.new_path
        and _added_line_count(diff_file) >= 3
    ]
    test_files = [path for path in changed_files if path.startswith("tests/") or "/tests/" in path or path.startswith("test_")]
    if not production_files or test_files:
        return []

    first_file = production_files[0]
    line = _first_added_line(parsed_diff, first_file)
    return [
        _finding(
            severity=Severity.LOW,
            category="test_coverage",
            file=first_file,
            line=line,
            title="Production change has no matching test update",
            evidence="Python production files changed, but this diff does not include test files.",
            recommendation="Add or update tests that exercise the changed behavior, or explain why tests are not required.",
            confidence=Confidence.LOW,
        )
    ]




def _first_added_line(parsed_diff: ParsedDiff, file_path: str) -> int:
    for diff_file in parsed_diff.files:
        if diff_file.new_path != file_path:
            continue
        for hunk in diff_file.hunks:
            for line in hunk.changed_lines:
                if line.kind == ChangedLineKind.ADDED and line.new_line_number is not None:
                    return line.new_line_number
    return 1


def _added_line_count(diff_file: object) -> int:
    return sum(
        1
        for hunk in getattr(diff_file, "hunks", [])
        for line in hunk.changed_lines
        if line.kind == ChangedLineKind.ADDED
    )


def _finding(
    *,
    severity: Severity,
    category: str,
    file: str,
    line: int,
    title: str,
    evidence: str,
    recommendation: str,
    confidence: Confidence,
) -> ReviewFinding:
    return ReviewFinding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        line_start=line,
        line_end=line,
        title=title,
        evidence=evidence,
        recommendation=recommendation,
        confidence=confidence,
        source=FindingSource.FAKE_MODEL,
    )


def _looks_like_secret(text: str) -> bool:
    return bool(
        _SECRET_ASSIGNMENT_RE.search(text)
        or _HEADER_SECRET_RE.search(text)
        or _TOKEN_PREFIX_RE.search(text)
        or _DB_URL_RE.search(text)
        or "PRIVATE KEY" in text
    )


def _hunk_mentions_cleanup(lines: Iterable[ChangedLine]) -> bool:
    return any("unlink(" in line.text or "remove(" in line.text for line in lines)


def _looks_like_db_connection(text: str) -> bool:
    patterns = ("sqlite3.connect(", "engine.connect(", "Session(", ".begin(")
    return any(pattern in text for pattern in patterns)


def _line_has_lifecycle_guard(text: str) -> bool:
    return "with " in text or "async with " in text


def _hunk_mentions_db_cleanup(lines: Iterable[ChangedLine]) -> bool:
    cleanup_terms = (".close(", ".commit(", ".rollback(", "with ", "async with ")
    return any(any(term in line.text for term in cleanup_terms) for line in lines)
