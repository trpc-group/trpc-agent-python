"""Deterministic review rules used by the bundled code-review skill."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import re

from .models import Finding
from .parser import ChangedLine
from .redactor import redact


def _finding(line: ChangedLine, *, severity: str, category: str, title: str, recommendation: str) -> Finding:
    evidence = redact(line.content.strip())
    key = f"{category}:{line.file}:{line.line}:{evidence}".encode()
    return Finding(severity, category, line.file, line.line, title, evidence, recommendation, 0.95,
                   fingerprint=hashlib.sha256(key).hexdigest())


def scan(lines: list[ChangedLine]) -> list[Finding]:
    findings: list[Finding] = []
    all_content = "\n".join(line.content for line in lines)
    for line in lines:
        content = line.content
        if re.search(r"sk-[A-Za-z0-9_-]{8,}|(?:api[_-]?key|token|password)\s*=\s*[\"']", content, re.I):
            findings.append(_finding(line, severity="critical", category="secret", title="Hard-coded credential",
                                     recommendation="Read the credential from an approved secret provider or environment variable."))
        if "aiohttp.ClientSession(" in content or "AsyncClient(" in content:
            findings.append(_finding(line, severity="high", category="async", title="Async client may not be closed",
                                     recommendation="Use async with, or close the client in a finally block."))
        if re.search(r"\b(?:engine|conn|connection)\.connect\(", content):
            findings.append(_finding(line, severity="high", category="database", title="Database connection lifecycle is unsafe",
                                     recommendation="Use a context manager so connections close on both success and failure paths."))
        if re.search(r"\b(?:tx|transaction)\.begin\(", content) and "rollback" not in all_content:
            findings.append(_finding(line, severity="high", category="database", title="Transaction has no rollback path",
                                     recommendation="Rollback in the exception path, or use an atomic transaction context manager."))
    changed_code = any(not line.file.startswith("tests/") and line.file.endswith(".py") for line in lines)
    changed_tests = any(line.file.startswith("tests/") and line.file.endswith(".py") for line in lines)
    if changed_code and not changed_tests:
        marker = next(line for line in lines if not line.file.startswith("tests/") and line.file.endswith(".py"))
        findings.append(_finding(marker, severity="medium", category="tests", title="Changed behavior has no matching test diff",
                                 recommendation="Add a focused regression or behavior test for this change."))
    return deduplicate(findings)


def deduplicate(findings: list[Finding]) -> list[Finding]:
    unique: dict[tuple[str, str, str], Finding] = {}
    for finding in findings:
        # One secret printed twice in the same changed file is one remediation task.
        # Evidence has already been redacted, so it is safe to use as the stable key.
        unique.setdefault((finding.category, finding.file, finding.evidence), finding)
    return list(unique.values())