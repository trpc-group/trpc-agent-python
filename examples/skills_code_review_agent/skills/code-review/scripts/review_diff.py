#!/usr/bin/env python3
#
# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
#
"""Deterministic unified-diff scanner used inside an isolated workspace."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_DIFF_BYTES = 2 * 1024 * 1024
MAX_FINDINGS = 200
SEVERITY_ORDER = {
    "critical": 5,
    "high": 4,
    "medium": 3,
    "low": 2,
    "info": 1,
}
SECRET_KEY_NAME = (
    r"(?:[a-z0-9]+[_-])*"
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password|passwd|pwd)"
)
SECRET_KEY_PREFIX = rf"(?:(?:\b{SECRET_KEY_NAME}\b)|(?:[\"']{SECRET_KEY_NAME}[\"']))\s*[:=]\s*"

KNOWN_SECRET_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|rk)-(?:live|test|proj)-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{16,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?i)[a-z][a-z0-9+.-]*://[^:/\s]+:[^@\s/]+@"),
)
SECRET_ASSIGNMENT = re.compile(
    rf"""(?ix)
    ({SECRET_KEY_PREFIX})
    ("[^"\r\n]{{4,}}"|'[^'\r\n]{{4,}}'|[^"'\s,;}}]{{4,}})
    """
)


@dataclass(frozen=True)
class AddedLine:
    """One candidate added line with target line mapping."""

    file: str
    line: int
    content: str
    hunk_id: int


@dataclass
class ChangedFile:
    """Parsed changed file."""

    path: str
    added: list[AddedLine]
    hunks: dict[int, list[str]]


def redact(value: str) -> tuple[str, int]:
    """Redact known credentials before output."""

    result = value
    count = 0
    for pattern in KNOWN_SECRET_PATTERNS:
        result, replaced = pattern.subn("[REDACTED]", result)
        count += replaced
    result, replaced = SECRET_ASSIGNMENT.subn(_redact_assignment, result)
    count += replaced
    return result[:500], count


def _redact_assignment(match: re.Match[str]) -> str:
    secret_value = match.group(2)
    quote = secret_value[0] if secret_value[0] in {'"', "'"} else ""
    return f"{match.group(1)}{quote}[REDACTED]{quote}"


def _diff_path(header: str) -> str:
    try:
        parts = shlex.split(header)
    except ValueError:
        return ""
    if len(parts) < 4:
        return ""
    path = parts[3]
    return path[2:] if path.startswith("b/") else path


def parse_unified_diff(text: str) -> list[ChangedFile]:
    """Parse files, hunks, target lines, and hunk context."""

    changed_files: list[ChangedFile] = []
    current: ChangedFile | None = None
    hunk_id = 0
    old_line = 0
    new_line = 0
    current_from_git_header = False
    hunk_pattern = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for raw_line in text.splitlines():
        if raw_line.startswith("diff --git "):
            path = _diff_path(raw_line)
            current = ChangedFile(path=path, added=[], hunks={})
            changed_files.append(current)
            current_from_git_header = True
            continue
        if raw_line.startswith("--- "):
            if current is None or not current_from_git_header:
                current = ChangedFile(path="", added=[], hunks={})
                changed_files.append(current)
            current_from_git_header = False
            continue
        if current is None:
            continue
        if raw_line.startswith("+++ "):
            header_path = raw_line[4:].split("\t", 1)[0]
            try:
                target = shlex.split(header_path)[0]
            except (ValueError, IndexError):
                target = header_path
            if target != "/dev/null":
                current.path = target[2:] if target.startswith("b/") else target
            continue
        match = hunk_pattern.match(raw_line)
        if match:
            hunk_id += 1
            old_line = int(match.group(1))
            new_line = int(match.group(2))
            current.hunks[hunk_id] = []
            continue
        if not hunk_id or hunk_id not in current.hunks:
            continue
        if raw_line.startswith("\\ No newline"):
            continue
        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            content = raw_line[1:]
            current.added.append(AddedLine(current.path, new_line, content, hunk_id))
            current.hunks[hunk_id].append(content)
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            old_line += 1
        elif raw_line.startswith(" "):
            current.hunks[hunk_id].append(raw_line[1:])
            old_line += 1
            new_line += 1

    return [item for item in changed_files if item.path]


def load_rules() -> list[dict[str, Any]]:
    rules_path = Path(__file__).resolve().parents[1] / "references" / "rules.json"
    payload = json.loads(rules_path.read_text(encoding="utf-8"))
    return payload["line_rules"]


def make_finding(
    *,
    severity: str,
    category: str,
    added: AddedLine,
    title: str,
    recommendation: str,
    confidence: float,
    source: str,
) -> tuple[dict[str, Any], int]:
    evidence, redactions = redact(added.content.strip())
    return {
        "severity": severity,
        "category": category,
        "file": added.file,
        "line": added.line,
        "title": title,
        "evidence": evidence,
        "recommendation": recommendation,
        "confidence": confidence,
        "source": f"skill:{source}",
    }, redactions


def scan_line_rules(
    changed_files: list[ChangedFile],
    rules: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    findings = []
    redaction_count = 0
    compiled = [(rule, re.compile(rule["pattern"])) for rule in rules]
    for changed_file in changed_files:
        for added in changed_file.added:
            for rule, pattern in compiled:
                if not pattern.search(added.content):
                    continue
                finding, redactions = make_finding(
                    severity=rule["severity"],
                    category=rule["category"],
                    added=added,
                    title=rule["title"],
                    recommendation=rule["recommendation"],
                    confidence=float(rule["confidence"]),
                    source=rule["id"],
                )
                findings.append(finding)
                redaction_count += redactions
    return findings, redaction_count


def scan_lifecycles(changed_files: list[ChangedFile]) -> tuple[list[dict[str, Any]], int]:
    """Evaluate resource, database, transaction, and blocking-async context."""

    findings = []
    redaction_count = 0
    connection_pattern = re.compile(
        r"(?i)(?:\b(?:sqlite3|psycopg2|pymysql|mysql|database|driverManager)\s*"
        r"(?:\.|::)\s*(?:connect|getConnection)|\bsql\.Open)\s*\("
    )
    transaction_pattern = re.compile(
        r"(?i)(?:\.(?:begin|begin_transaction|beginTransaction|BeginTx)\s*\(|\bSTART\s+TRANSACTION\b)"
    )
    client_pattern = re.compile(
        r"\b(?:aiohttp\.ClientSession|httpx\.(?:Client|AsyncClient)|requests\.Session)\s*\("
    )

    for changed_file in changed_files:
        for added in changed_file.added:
            hunk_lines = changed_file.hunks.get(added.hunk_id, [])
            hunk_text = "\n".join(hunk_lines)
            lowered = hunk_text.lower()

            if re.search(r"^\s*(?!with\b)[A-Za-z_][A-Za-z0-9_]*\s*=\s*open\s*\(", added.content):
                if ".close(" not in lowered and "with open(" not in lowered:
                    finding, count = make_finding(
                        severity="high",
                        category="resource_leak",
                        added=added,
                        title="File handle has no guaranteed close",
                        recommendation="Use a context manager or close the handle in a finally block.",
                        confidence=0.93,
                        source="RES-CTX-001",
                    )
                    findings.append(finding)
                    redaction_count += count

            if client_pattern.search(added.content):
                if ".close(" not in lowered and ".aclose(" not in lowered and "async with" not in lowered:
                    finding, count = make_finding(
                        severity="high",
                        category="resource_leak",
                        added=added,
                        title="Client resource may leak",
                        recommendation="Use a context manager or guarantee client shutdown in finally.",
                        confidence=0.90,
                        source="RES-CTX-002",
                    )
                    findings.append(finding)
                    redaction_count += count

            if connection_pattern.search(added.content):
                safe = any(token in lowered for token in (".close(", "with ", "defer "))
                if not safe:
                    finding, count = make_finding(
                        severity="high",
                        category="database_lifecycle",
                        added=added,
                        title="Database connection may remain open",
                        recommendation="Use a scoped connection context and guarantee close on every path.",
                        confidence=0.95,
                        source="DB-CTX-001",
                    )
                    findings.append(finding)
                    redaction_count += count

            if transaction_pattern.search(added.content):
                safe = any(token in lowered for token in (".commit(", ".rollback(", "with ", "defer "))
                if not safe:
                    finding, count = make_finding(
                        severity="high",
                        category="database_lifecycle",
                        added=added,
                        title="Transaction has no visible completion path",
                        recommendation=(
                            "Commit on success and rollback in every error path, "
                            "preferably in a managed scope."
                        ),
                        confidence=0.93,
                        source="DB-CTX-002",
                    )
                    findings.append(finding)
                    redaction_count += count

            if re.search(r"\btime\.sleep\s*\(", added.content) and "async def " in hunk_text:
                finding, count = make_finding(
                    severity="high",
                    category="async_error",
                    added=added,
                    title="Blocking sleep in asynchronous code",
                    recommendation="Use the runtime's non-blocking sleep and await it.",
                    confidence=0.96,
                    source="ASYNC-CTX-001",
                )
                findings.append(finding)
                redaction_count += count

    return findings, redaction_count


def scan_test_coverage(changed_files: list[ChangedFile]) -> list[dict[str, Any]]:
    test_changed = any(
        re.search(r"(^|/)(?:tests?|spec)(/|_)|(?:_test|test_|\.spec\.)", item.path, re.IGNORECASE)
        for item in changed_files)
    if test_changed:
        return []

    findings = []
    source_extensions = {
        ".c",
        ".cc",
        ".cpp",
        ".go",
        ".java",
        ".js",
        ".kt",
        ".py",
        ".rs",
        ".ts",
    }
    for changed_file in changed_files:
        if Path(changed_file.path).suffix.lower() not in source_extensions:
            continue
        if len(changed_file.added) < 6:
            continue
        added = changed_file.added[0]
        finding, _ = make_finding(
            severity="medium",
            category="test_coverage",
            added=added,
            title="Behavior change has no accompanying test",
            recommendation="Add focused tests for the new branches, boundary conditions, and failure paths.",
            confidence=0.86,
            source="TEST-001",
        )
        findings.append(finding)
    return findings


def deduplicate(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, int, str], dict[str, Any]] = {}
    for finding in findings:
        key = (finding["file"], int(finding["line"]), finding["category"])
        current = unique.get(key)
        if current is None:
            unique[key] = finding
            continue
        old_score = (SEVERITY_ORDER.get(current["severity"], 0), float(current["confidence"]))
        new_score = (SEVERITY_ORDER.get(finding["severity"], 0), float(finding["confidence"]))
        if new_score > old_score:
            unique[key] = finding
    return sorted(
        unique.values(),
        key=lambda item: (
            -SEVERITY_ORDER.get(item["severity"], 0),
            item["file"],
            item["line"],
            item["category"],
        ),
    )[:MAX_FINDINGS]


def main() -> int:
    if len(sys.argv) == 1:
        work_dir = Path(os.environ["WORK_DIR"])
        output_dir = Path(os.environ["OUTPUT_DIR"])
        input_path = work_dir / "inputs" / "review.diff"
        output_path = output_dir / "review_findings.json"
    elif len(sys.argv) == 3:
        input_path = Path(sys.argv[1])
        output_path = Path(sys.argv[2])
    else:
        print("usage: review_diff.py INPUT_DIFF OUTPUT_JSON", file=sys.stderr)
        return 2

    raw = input_path.read_bytes()
    if len(raw) > MAX_DIFF_BYTES:
        print(f"input exceeds {MAX_DIFF_BYTES} bytes", file=sys.stderr)
        return 2

    changed_files = parse_unified_diff(raw.decode("utf-8", errors="replace"))
    line_findings, line_redactions = scan_line_rules(changed_files, load_rules())
    lifecycle_findings, lifecycle_redactions = scan_lifecycles(changed_files)
    test_findings = scan_test_coverage(changed_files)
    findings = deduplicate(line_findings + lifecycle_findings + test_findings)

    payload = {
        "findings": findings,
        "stats": {
            "files_scanned": len(changed_files),
            "added_lines_scanned": sum(len(item.added) for item in changed_files),
            "finding_count": len(findings),
        },
        "redaction_count": line_redactions + lifecycle_redactions,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    print(
        f"reviewed {payload['stats']['files_scanned']} files, "
        f"found {payload['stats']['finding_count']} candidates"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
