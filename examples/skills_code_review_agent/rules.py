"""Deterministic high-signal review rules."""

from __future__ import annotations

import re
from collections.abc import Iterable

from models import ChangedLine
from models import Finding


class RuleEngine:
    """Scan added lines and return deduplicated findings."""

    RULES = (
        (
            "SEC001",
            "security",
            "critical",
            re.compile(r"\b(eval|exec)\s*\(|pickle\.loads\s*\(|os\.system\s*\(|shell\s*=\s*True"),
            "Unsafe code or command execution",
            "Remove dynamic execution or use a fixed argv allowlist in an isolated process.",
            0.96,
        ),
        (
            "RES001",
            "resource_leak",
            "high",
            re.compile(r"(?<!with )\bopen\s*\(|requests\.(get|post|put|delete)\s*\([^\n]*(?<!timeout=)\)$"),
            "Resource lifetime is not bounded",
            "Use a context manager and set explicit network timeouts.",
            0.86,
        ),
        (
            "DB001",
            "database_lifecycle",
            "high",
            re.compile(r"(?<!with )\b(sqlite3|psycopg|pymysql|aiomysql)\.connect\s*\("),
            "Database connection may leak",
            "Use a context-managed transaction or close the connection in finally.",
            0.9,
        ),
        (
            "SECRET001",
            "sensitive_information",
            "critical",
            re.compile(
                r"(?i)(?:(api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*['\"][^'\"]{6,}['\"]|"
                r"\bAKIA[0-9A-Z]{16}\b|\bgh[pousr]_[A-Za-z0-9]{8,}\b|\bsk-[A-Za-z0-9_-]{8,}\b)"
            ),
            "Hard-coded credential in changed code",
            "Remove the credential, rotate it, and load an opaque secret reference at runtime.",
            0.99,
        ),
    )

    def scan(self, lines: Iterable[ChangedLine]) -> list[Finding]:
        lines = list(lines)
        findings = []
        async_context: dict[str, bool] = {}
        for line in lines:
            stripped = line.text.strip()
            if re.match(r"async\s+def\s+", stripped):
                async_context[line.file] = True
            if async_context.get(line.file) and "time.sleep(" in line.text:
                findings.append(self._finding(
                    line, "ASYNC001", "async_error", "high",
                    "Blocking sleep inside async code",
                    "Replace time.sleep with await asyncio.sleep.", 0.94,
                ))
            if "asyncio.create_task(" in line.text and "=" not in line.text:
                findings.append(self._finding(
                    line, "ASYNC001", "async_error", "medium",
                    "Created task has no owner",
                    "Retain and await or cancel the task during shutdown.", 0.82,
                ))
            for rule_id, category, severity, pattern, title, recommendation, confidence in self.RULES:
                if pattern.search(line.text):
                    findings.append(self._finding(
                        line, rule_id, category, severity, title, recommendation, confidence,
                    ))
        findings.extend(self._test_coverage_warning(lines))
        return self.deduplicate(findings)

    @staticmethod
    def _finding(
        line: ChangedLine,
        rule_id: str,
        category: str,
        severity: str,
        title: str,
        recommendation: str,
        confidence: float,
    ) -> Finding:
        return Finding(
            severity=severity,
            category=category,
            file=line.file,
            line=line.line,
            title=title,
            evidence=line.text.strip()[:200],
            recommendation=recommendation,
            confidence=confidence,
            source=f"static_rule:{rule_id}",
            rule_id=rule_id,
        )

    @staticmethod
    def _test_coverage_warning(lines: list[ChangedLine]) -> list[Finding]:
        files = {line.file for line in lines}
        production = sorted(
            file for file in files
            if file.endswith(".py") and not file.startswith("tests/") and "/test_" not in file
        )
        tests_changed = any(file.startswith("tests/") or "/test_" in file for file in files)
        if not production or tests_changed:
            return []
        line = next(item for item in lines if item.file == production[0])
        return [Finding(
            severity="low",
            category="test_missing",
            file=line.file,
            line=line.line,
            title="Production change has no corresponding test change",
            evidence=f"changed production file: {line.file}",
            recommendation="Add focused positive, negative, and regression tests for the changed behavior.",
            confidence=0.65,
            source="heuristic:TEST001",
            rule_id="TEST001",
        )]

    @staticmethod
    def deduplicate(findings: Iterable[Finding]) -> list[Finding]:
        unique: dict[tuple[str, int, str], Finding] = {}
        for finding in findings:
            key = (finding.file, finding.line, finding.category)
            previous = unique.get(key)
            if previous is None or finding.confidence > previous.confidence:
                unique[key] = finding
        return sorted(unique.values(), key=lambda item: (item.file, item.line, item.category))
