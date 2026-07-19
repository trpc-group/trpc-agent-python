"""Deterministic rule engine for code review findings."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .review_types import (
    ChangedFile,
    DiffHunk,
    DiffLine,
    DiffLineType,
    FindingSource,
    ParsedDiff,
    ReviewCategory,
    ReviewFinding,
    ReviewSeverity,
)

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str, str, ReviewSeverity, float]] = [
    (
        re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\b\s*[:=]\s*['\"][^'\"]{6,}['\"]"),
        "Hard-coded secret detected",
        "Sensitive credentials should not be committed to source control.",
        ReviewSeverity.HIGH,
        0.98,
    ),
    (
        re.compile(r"AKIA[0-9A-Z]{16}"),
        "AWS access key detected",
        "Remove the credential from code and load it from a secure secret manager.",
        ReviewSeverity.CRITICAL,
        0.99,
    ),
    (
        re.compile(r"(?i)Bearer\s+[A-Za-z0-9._\-]{8,}"),
        "Bearer token detected",
        "Do not embed bearer tokens in code or fixtures.",
        ReviewSeverity.HIGH,
        0.97,
    ),
    (
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        "Private key material detected",
        "Rotate the key immediately and move secret material out of the repository.",
        ReviewSeverity.CRITICAL,
        1.0,
    ),
]

_CODE_FILE_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".java",
    ".rb",
    ".rs",
    ".php",
    ".cpp",
    ".c",
    ".h",
    ".hpp",
}


def run_rule_engine(parsed_diff: ParsedDiff) -> list[ReviewFinding]:
    """Run deterministic review rules on a parsed diff."""

    findings: list[ReviewFinding] = []
    findings.extend(_check_missing_tests(parsed_diff))

    for changed_file in parsed_diff.files:
        findings.extend(_check_security_rules(changed_file))
        findings.extend(_check_secret_rules(changed_file))
        findings.extend(_check_async_rules(changed_file))
        findings.extend(_check_resource_leak_rules(changed_file))
        findings.extend(_check_db_lifecycle_rules(changed_file))

    return findings


def _check_security_rules(changed_file: ChangedFile) -> list[ReviewFinding]:
    """Detect high-confidence security patterns in newly added code."""

    findings: list[ReviewFinding] = []
    for hunk, index, line in _iter_added_lines(changed_file):
        text = line.text.strip()
        context = _line_context(hunk, index)

        if re.search(r"\beval\s*\(", text):
            findings.append(
                _make_finding(
                    category=ReviewCategory.SECURITY,
                    severity=ReviewSeverity.HIGH,
                    changed_file=changed_file,
                    line=line,
                    title="Use of eval introduces code execution risk",
                    evidence=context,
                    recommendation=(
                        "Replace eval with explicit parsing, a whitelist-based dispatcher, "
                        "or a safe literal parser."
                    ),
                    confidence=0.98,
                )
            )
        if re.search(r"\bexec\s*\(", text):
            findings.append(
                _make_finding(
                    category=ReviewCategory.SECURITY,
                    severity=ReviewSeverity.HIGH,
                    changed_file=changed_file,
                    line=line,
                    title="Use of exec introduces arbitrary code execution risk",
                    evidence=context,
                    recommendation="Avoid exec and replace it with explicit callable dispatch.",
                    confidence=0.98,
                )
            )
        if "pickle.loads(" in text:
            findings.append(
                _make_finding(
                    category=ReviewCategory.SECURITY,
                    severity=ReviewSeverity.HIGH,
                    changed_file=changed_file,
                    line=line,
                    title="pickle.loads on untrusted input is unsafe",
                    evidence=context,
                    recommendation=(
                        "Avoid deserializing untrusted pickle payloads; prefer a safe format such as JSON."
                    ),
                    confidence=0.96,
                )
            )
        if "yaml.load(" in text and "safe_load" not in text and "SafeLoader" not in context:
            findings.append(
                _make_finding(
                    category=ReviewCategory.SECURITY,
                    severity=ReviewSeverity.HIGH,
                    changed_file=changed_file,
                    line=line,
                    title="yaml.load without a safe loader is unsafe",
                    evidence=context,
                    recommendation="Use yaml.safe_load or pass SafeLoader explicitly.",
                    confidence=0.94,
                )
            )
        if "verify=False" in text:
            findings.append(
                _make_finding(
                    category=ReviewCategory.SECURITY,
                    severity=ReviewSeverity.MEDIUM,
                    changed_file=changed_file,
                    line=line,
                    title="TLS certificate verification is disabled",
                    evidence=context,
                    recommendation="Keep certificate verification enabled or document a controlled test-only exception.",
                    confidence=0.88,
                )
            )
        if (
            ("shell=True" in text or "subprocess." in text)
            and "shell=True" in context
            and re.search(
                r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\s*\(",
                context,
            )
        ):
            findings.append(
                _make_finding(
                    category=ReviewCategory.SECURITY,
                    severity=ReviewSeverity.HIGH,
                    changed_file=changed_file,
                    line=line,
                    title="subprocess call enables shell execution",
                    evidence=context,
                    recommendation="Pass an argument list and avoid shell=True unless a reviewed shell command is unavoidable.",
                    confidence=0.95,
                )
            )

    return findings


def _check_secret_rules(changed_file: ChangedFile) -> list[ReviewFinding]:
    """Detect obvious hard-coded secrets and credential material."""

    findings: list[ReviewFinding] = []
    for hunk, index, line in _iter_added_lines(changed_file):
        context = _line_context(hunk, index)
        for pattern, title, recommendation, severity, confidence in _SECRET_PATTERNS:
            if pattern.search(line.text):
                findings.append(
                    _make_finding(
                        category=ReviewCategory.SECRET,
                        severity=severity,
                        changed_file=changed_file,
                        line=line,
                        title=title,
                        evidence=context,
                        recommendation=recommendation,
                        confidence=confidence,
                    )
                )
    return findings


def _check_async_rules(changed_file: ChangedFile) -> list[ReviewFinding]:
    """Detect common async anti-patterns with controlled confidence."""

    findings: list[ReviewFinding] = []
    for hunk, index, line in _iter_added_lines(changed_file):
        text = line.text.strip()
        context = _line_context(hunk, index)

        if re.match(r"asyncio\.create_task\s*\(", text):
            findings.append(
                _make_finding(
                    category=ReviewCategory.ASYNC,
                    severity=ReviewSeverity.MEDIUM,
                    changed_file=changed_file,
                    line=line,
                    title="Detached asyncio task is created without lifecycle tracking",
                    evidence=context,
                    recommendation=(
                        "Store the task handle, await completion, or add explicit cancellation and error handling."
                    ),
                    confidence=0.72,
                )
            )

        if text.startswith("except Exception"):
            next_line = _next_meaningful_new_line(hunk.lines, index)
            if next_line is not None and next_line.text.strip() == "pass":
                findings.append(
                    _make_finding(
                        category=ReviewCategory.ASYNC,
                        severity=ReviewSeverity.MEDIUM,
                        changed_file=changed_file,
                        line=line,
                        title="Broad exception is swallowed silently",
                        evidence=_line_context(hunk, index, radius=2),
                        recommendation="Handle expected exception types explicitly and surface unexpected failures.",
                        confidence=0.68,
                    )
                )

    return findings


def _check_resource_leak_rules(changed_file: ChangedFile) -> list[ReviewFinding]:
    """Detect likely resource lifecycle issues outside database-specific patterns."""

    findings: list[ReviewFinding] = []
    for hunk, index, line in _iter_added_lines(changed_file):
        text = line.text.strip()
        context = _line_context(hunk, index)

        if "open(" in text and not text.startswith("with open("):
            findings.append(
                _make_finding(
                    category=ReviewCategory.RESOURCE_LEAK,
                    severity=ReviewSeverity.MEDIUM,
                    changed_file=changed_file,
                    line=line,
                    title="File handle is opened without a context manager",
                    evidence=context,
                    recommendation="Wrap file access in `with open(...)` so handles are closed automatically.",
                    confidence=0.78,
                )
            )

        if _contains_resource_constructor(text) and not text.startswith(("with ", "async with ")):
            findings.append(
                _make_finding(
                    category=ReviewCategory.RESOURCE_LEAK,
                    severity=ReviewSeverity.MEDIUM,
                    changed_file=changed_file,
                    line=line,
                    title="Client or session may not be closed",
                    evidence=context,
                    recommendation=(
                        "Prefer `with` / `async with` for client and session objects, or ensure close() is called."
                    ),
                    confidence=0.66,
                )
            )

    return findings


def _check_db_lifecycle_rules(changed_file: ChangedFile) -> list[ReviewFinding]:
    """Detect likely transaction or connection lifecycle issues."""

    findings: list[ReviewFinding] = []
    file_added_text = _all_added_text(changed_file)
    has_close = ".close(" in file_added_text or "close()" in file_added_text
    has_commit = ".commit(" in file_added_text or "commit()" in file_added_text
    has_rollback = ".rollback(" in file_added_text or "rollback()" in file_added_text

    for hunk, index, line in _iter_added_lines(changed_file):
        text = line.text.strip()
        context = _line_context(hunk, index)

        if _contains_db_connect(text) and not text.startswith("with "):
            confidence = 0.74 if has_close else 0.86
            findings.append(
                _make_finding(
                    category=ReviewCategory.DB_LIFECYCLE,
                    severity=ReviewSeverity.MEDIUM if has_close else ReviewSeverity.HIGH,
                    changed_file=changed_file,
                    line=line,
                    title="Database connection is created without clear lifecycle management",
                    evidence=context,
                    recommendation=(
                        "Use a context manager or ensure the connection is closed in all execution paths."
                    ),
                    confidence=confidence,
                )
            )

        if _contains_transaction_start(text) and not (has_commit and has_rollback):
            findings.append(
                _make_finding(
                    category=ReviewCategory.DB_LIFECYCLE,
                    severity=ReviewSeverity.MEDIUM,
                    changed_file=changed_file,
                    line=line,
                    title="Transaction handling appears incomplete",
                    evidence=context,
                    recommendation="Ensure transactions are committed on success and rolled back on failure.",
                    confidence=0.67,
                )
            )

    return findings


def _check_missing_tests(parsed_diff: ParsedDiff) -> list[ReviewFinding]:
    """Flag code changes that do not include corresponding test updates."""

    changed_paths = parsed_diff.changed_paths
    code_paths = [path for path in changed_paths if _is_production_code_path(path)]
    has_test_change = any(_is_test_path(path) for path in changed_paths)

    if not code_paths or has_test_change:
        return []

    return [
        ReviewFinding(
            severity=ReviewSeverity.MEDIUM,
            category=ReviewCategory.TEST_MISSING,
            file=code_paths[0],
            line=None,
            title="Production code changed without test updates",
            evidence="Changed code paths: " + ", ".join(code_paths[:5]),
            recommendation="Add or update tests that cover the new behavior and failure paths.",
            confidence=0.62,
            source=FindingSource.RULE_ENGINE,
        )
    ]


def _iter_added_lines(changed_file: ChangedFile) -> Iterable[tuple[DiffHunk, int, DiffLine]]:
    """Yield added lines together with their hunk and index."""

    for hunk in changed_file.hunks:
        for index, line in enumerate(hunk.lines):
            if line.line_type == DiffLineType.ADD and line.new_line_no is not None:
                yield hunk, index, line


def _line_context(hunk: DiffHunk, index: int, radius: int = 1) -> str:
    """Return a compact context snippet around a line."""

    start = max(0, index - radius)
    end = min(len(hunk.lines), index + radius + 1)
    return "\n".join(hunk.lines[offset].raw_line for offset in range(start, end))


def _next_meaningful_new_line(lines: list[DiffLine], index: int) -> DiffLine | None:
    """Return the next added or context line with a new-file line number."""

    for offset in range(index + 1, len(lines)):
        candidate = lines[offset]
        if candidate.new_line_no is not None:
            return candidate
    return None


def _contains_resource_constructor(text: str) -> bool:
    """Return whether a line creates a client or session likely needing cleanup."""

    return any(
        token in text
        for token in (
            "requests.Session(",
            "httpx.Client(",
            "httpx.AsyncClient(",
            "aiohttp.ClientSession(",
            "ClientSession(",
        )
    )


def _contains_db_connect(text: str) -> bool:
    """Return whether a line appears to create a database connection or session."""

    return any(
        token in text
        for token in (
            "sqlite3.connect(",
            "psycopg.connect(",
            "psycopg2.connect(",
            "Session(",
            "sessionmaker(",
            ".cursor(",
        )
    )


def _contains_transaction_start(text: str) -> bool:
    """Return whether a line appears to begin a transaction."""

    return any(token in text for token in (".begin(", "BEGIN", "transaction("))


def _all_added_text(changed_file: ChangedFile) -> str:
    """Return all added text for a changed file."""

    return "\n".join(line.text for _, _, line in _iter_added_lines(changed_file))


def _is_test_path(path: str) -> bool:
    """Return whether a changed path looks like a test file."""

    normalized = path.replace("\\", "/")
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    return (
        "/tests/" in f"/{normalized}/"
        or basename.startswith("test_")
        or basename.endswith("_test.py")
        or basename.endswith(".spec.ts")
        or basename.endswith(".spec.tsx")
        or basename.endswith(".test.ts")
        or basename.endswith(".test.tsx")
    )


def _is_production_code_path(path: str) -> bool:
    """Return whether a changed path should count as production code."""

    normalized = path.replace("\\", "/")
    if _is_test_path(normalized):
        return False
    if any(part in normalized for part in ("/docs/", "/migrations/", "/examples/")):
        return False
    return any(normalized.endswith(ext) for ext in _CODE_FILE_EXTENSIONS)


def _make_finding(
    *,
    category: ReviewCategory,
    severity: ReviewSeverity,
    changed_file: ChangedFile,
    line: DiffLine,
    title: str,
    evidence: str,
    recommendation: str,
    confidence: float,
) -> ReviewFinding:
    """Create a normalized rule-engine finding."""

    return ReviewFinding(
        severity=severity,
        category=category,
        file=changed_file.display_path,
        line=line.new_line_no,
        title=title,
        evidence=evidence,
        recommendation=recommendation,
        confidence=confidence,
        source=FindingSource.RULE_ENGINE,
    )
