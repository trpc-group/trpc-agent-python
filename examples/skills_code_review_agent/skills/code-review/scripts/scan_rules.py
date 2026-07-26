#!/usr/bin/env python3
"""Deterministic scanner for canonical code-review input."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Iterable

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_OUTPUT_LINES = 2_000
MAX_EVIDENCE_LENGTH = 240
MIN_SECRET_LENGTH = 8
REDACTION_MARKER = "[REDACTED]"

TEST_PATH_PATTERN = re.compile(r"(^|/)(tests?|specs?)(/|$)|(^|/)test_[^/]+\.py$")
EXEMPT_PATH_PATTERN = re.compile(r"\.(md|rst|txt|json|ya?ml|toml)$|(^|/)(fixtures?|generated)(/|$)")
ASYNC_CONTEXT_PATTERN = re.compile(r"\basync\s+def\b")
SAFE_RESOURCE_PATTERN = re.compile(r"\b(?:async\s+)?with\b")
SECRET_PATTERN = re.compile(
    r"(?ix)"
    r"[\"']?(?:api[_-]?key|access[_-]?token|secret|password)[\"']?"
    r"\s*[:=]\s*[\"'](?P<value>[^\"']{8,})[\"']"
    r"|bearer\s+(?P<bearer>[A-Za-z0-9._~+/=-]{8,})"
    r"|(?P<url>https?://[^/\s:@]+):(?P<url_secret>[^@\s/]+)@", )
PRIVATE_KEY_PATTERN = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")
TOKEN_PATTERN = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[A-Za-z0-9]{30,}|"
    r"sk-[A-Za-z0-9_-]{20,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,})\b", )
BEHAVIOR_PATTERN = re.compile(
    r"^\s*(?:async\s+def|def|class|if|for|while|return|raise|yield|await)\b"
    r"|^\s*\w+(?:\.\w+)*\s*=.*\("
    r"|^\s*\w+(?:\.\w+)*\s*\(", )
SECRET_NAME_PATTERN = re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret|password)\b")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)[\"']?(?:api[_-]?key|access[_-]?token|secret|password)[\"']?"
    r"\s*[:=]\s*(?P<value>.+)$", )
QUOTED_CHUNK_PATTERN = re.compile(r"[\"']([^\"']+)[\"']")
PLACEHOLDER_PATTERN = re.compile(r"(?i)example|placeholder|changeme|dummy|redacted")
SECRET_VALUE_GROUPS = ("value", "bearer", "url_secret")


@dataclass(frozen=True)
class Rule:
    """One regex-backed changed-line rule."""

    rule_id: str
    pattern: re.Pattern[str]
    finding: dict[str, Any]
    flags: frozenset[str] = frozenset()


RULES = (
    Rule(
        "security.dynamic-execution",
        re.compile(r"\b(?:eval|exec|pickle\.loads)\s*\("),
        {
            "category": "security",
            "severity": "high",
            "title": "Dynamic execution on changed input",
            "recommendation": "Replace dynamic execution with a typed parser or strict allowlist.",
            "confidence": 0.93,
        },
    ),
    Rule(
        "security.shell-true",
        re.compile(r"\b(?:subprocess\.\w+|Popen)\s*\([^)]*\bshell\s*=\s*True"),
        {
            "category": "security",
            "severity": "critical",
            "title": "Shell execution enabled",
            "recommendation": "Pass a fixed argv list and keep shell execution disabled.",
            "confidence": 0.98,
        },
    ),
    Rule(
        "security.unsafe-yaml",
        re.compile(r"\byaml\.load\s*\((?![^)]*(?:SafeLoader|safe_load))"),
        {
            "category": "security",
            "severity": "high",
            "title": "Unsafe YAML deserialization",
            "recommendation": "Use yaml.safe_load or SafeLoader.",
            "confidence": 0.94,
        },
    ),
    Rule(
        "security.sql-interpolation",
        re.compile(r"\.execute\s*\(\s*(?:f[\"']|[^)]*\.format\s*\(|[^)]*%\s*[\w(])"),
        {
            "category": "security",
            "severity": "high",
            "title": "SQL statement uses string interpolation",
            "recommendation": "Use a parameterized query and bind values separately.",
            "confidence": 0.91,
        },
    ),
    Rule(
        "async.blocking-sleep",
        re.compile(r"\btime\.sleep\s*\("),
        {
            "category": "async_error",
            "severity": "medium",
            "title": "Blocking sleep in async code",
            "recommendation": "Use await asyncio.sleep instead.",
            "confidence": 0.95,
        },
        frozenset({"async"}),
    ),
    Rule(
        "async.unawaited-call",
        re.compile(r"^(?!\s*(?:await|return\s+await)\b).*\b(?:fetch_async|request_async|execute_async)\s*\("),
        {
            "category": "async_error",
            "severity": "medium",
            "title": "Async call is not awaited",
            "recommendation": "Await the coroutine or schedule and track it explicitly.",
            "confidence": 0.88,
        },
        frozenset({"async"}),
    ),
    Rule(
        "async.sync-http",
        re.compile(r"\b(?:requests|urllib\.request|httpx)\.(?:get|post|put|delete|request)\s*\("),
        {
            "category": "async_error",
            "severity": "medium",
            "title": "Synchronous HTTP call in async code",
            "recommendation": "Use an async HTTP client and await the request.",
            "confidence": 0.88,
        },
        frozenset({"async"}),
    ),
    Rule(
        "async.blocking-process",
        re.compile(r"\bsubprocess\.(?:run|call|check_call|check_output)\s*\("),
        {
            "category": "async_error",
            "severity": "medium",
            "title": "Blocking subprocess call in async code",
            "recommendation": "Use asyncio subprocess APIs and await completion.",
            "confidence": 0.86,
        },
        frozenset({"async"}),
    ),
    Rule(
        "resource.unmanaged-file",
        re.compile(r"=\s*open\s*\("),
        {
            "category": "resource_leak",
            "severity": "medium",
            "title": "File opened without managed lifetime",
            "recommendation": "Use a with statement or close the file in finally.",
            "confidence": 0.82,
        },
        frozenset({"unmanaged"}),
    ),
    Rule(
        "resource.unmanaged-process",
        re.compile(r"=\s*(?:subprocess\.)?Popen\s*\("),
        {
            "category": "resource_leak",
            "severity": "medium",
            "title": "Process lifetime is unmanaged",
            "recommendation": "Use a context manager and enforce wait/terminate cleanup.",
            "confidence": 0.80,
        },
        frozenset({"unmanaged"}),
    ),
    Rule(
        "resource.unmanaged-lock",
        re.compile(r"(?:await\s+)?\w+\.acquire\s*\("),
        {
            "category": "resource_leak",
            "severity": "medium",
            "title": "Lock acquisition lacks managed release",
            "recommendation": "Use a context manager or release the lock in finally.",
            "confidence": 0.78,
        },
        frozenset({"unmanaged"}),
    ),
    Rule(
        "db.unmanaged-connection",
        re.compile(r"=\s*(?:engine|db|pool)\.(?:connect|acquire)\s*\("),
        {
            "category": "db_lifecycle",
            "severity": "high",
            "title": "Database connection lifetime is unmanaged",
            "recommendation": "Use a managed transaction/context and guarantee rollback or close.",
            "confidence": 0.90,
        },
        frozenset({"unmanaged"}),
    ),
    Rule(
        "db.unmanaged-cursor",
        re.compile(r"=\s*\w+\.cursor\s*\("),
        {
            "category": "db_lifecycle",
            "severity": "medium",
            "title": "Database cursor lifetime is unmanaged",
            "recommendation": "Use a context manager or close the cursor in finally.",
            "confidence": 0.86,
        },
        frozenset({"unmanaged"}),
    ),
    Rule(
        "db.unmanaged-transaction",
        re.compile(r"=\s*(?:connection|session|db)\.begin\s*\("),
        {
            "category": "db_lifecycle",
            "severity": "high",
            "title": "Database transaction lifetime is unmanaged",
            "recommendation": "Use a transaction context and rollback on every failure path.",
            "confidence": 0.88,
        },
        frozenset({"unmanaged"}),
    ),
    Rule(
        "db.commit-in-except",
        re.compile(r"\.(?:commit)\s*\("),
        {
            "category": "db_lifecycle",
            "severity": "high",
            "title": "Transaction committed from exception path",
            "recommendation": "Rollback on failure and commit only after successful work.",
            "confidence": 0.84,
        },
        frozenset({"except"}),
    ),
)


def _load_input(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError("review input exceeds configured limit")
    if b"\x00" in data:
        raise ValueError("review input contains NUL bytes")
    payload = json.loads(data)
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        raise ValueError("review input must contain a files array")
    return payload


def _mask_secret(text: str) -> str:
    masked = PRIVATE_KEY_PATTERN.sub(REDACTION_MARKER, text)
    masked = TOKEN_PATTERN.sub(REDACTION_MARKER, masked)

    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        for secret in match.groupdict().values():
            if secret and len(secret) >= MIN_SECRET_LENGTH:
                value = value.replace(secret, REDACTION_MARKER)
        return value

    return SECRET_PATTERN.sub(replace, masked)[:MAX_EVIDENCE_LENGTH]


def _contains_secret(content: str, context: str) -> bool:
    secret_match = SECRET_PATTERN.search(content)
    if secret_match:
        values = [secret_match.group(name) for name in SECRET_VALUE_GROUPS if secret_match.group(name)]
        return any(not PLACEHOLDER_PATTERN.search(item) for item in values)
    if TOKEN_PATTERN.search(content):
        return True
    if PRIVATE_KEY_PATTERN.search(content):
        return True
    if not SECRET_NAME_PATTERN.search(content):
        return False
    assignment = SECRET_ASSIGNMENT_PATTERN.search(content)
    if not assignment:
        return False
    chunks = QUOTED_CHUNK_PATTERN.findall(assignment.group("value"))
    combined = "".join(chunks)
    return len(combined) >= MIN_SECRET_LENGTH and not PLACEHOLDER_PATTERN.search(combined)


def _finding(rule: Rule, file_path: str, line: int | None, evidence: str) -> dict[str, Any]:
    return {
        "severity": rule.finding["severity"],
        "category": rule.finding["category"],
        "file": file_path,
        "line": line,
        "title": rule.finding["title"],
        "evidence": _mask_secret(evidence),
        "recommendation": rule.finding["recommendation"],
        "confidence": rule.finding["confidence"],
        "source": f"skill:code-review/{rule.rule_id}",
    }


def _iter_added_lines(review_file: dict[str, Any]) -> Iterable[tuple[int | None, str, str]]:
    for hunk in review_file.get("hunks", []):
        context = "\n".join(
            str(line.get("content", "")) for line in hunk.get("lines", []) if line.get("kind") != "deleted")
        for line in hunk.get("lines", []):
            if line.get("kind") == "added":
                yield line.get("new_line"), str(line.get("content", "")), context


def _rule_matches(rule: Rule, content: str, context: str) -> bool:
    if not rule.pattern.search(content):
        return False
    if "async" in rule.flags and not ASYNC_CONTEXT_PATTERN.search(context):
        return False
    if "except" in rule.flags and "except" not in context:
        return False
    if "unmanaged" in rule.flags and _has_visible_cleanup(content, context):
        return False
    return True


def _has_visible_cleanup(content: str, context: str) -> bool:
    if SAFE_RESOURCE_PATTERN.search(content):
        return True
    assignment = re.match(r"\s*(\w+)\s*=", content)
    target = assignment.group(1) if assignment else None
    if target:
        cleanup = re.compile(rf"\b{re.escape(target)}\.(?:close|release|wait|terminate)\s*\(")
        return bool(cleanup.search(context))
    lock = re.search(r"\b(\w+)\.acquire\s*\(", content)
    if lock:
        return bool(re.search(rf"\b{re.escape(lock.group(1))}\.release\s*\(", context))
    return False


def _scan_line_rules(review_file: dict[str, Any]) -> list[dict[str, Any]]:
    path = str(review_file.get("new_path") or review_file.get("old_path") or "")
    findings: list[dict[str, Any]] = []
    for line_number, content, context in _iter_added_lines(review_file):
        for rule in RULES:
            if _rule_matches(rule, content, context):
                findings.append(_finding(rule, path, line_number, content))
        if _contains_secret(content, context):
            secret_rule = Rule(
                "secret.literal",
                SECRET_PATTERN,
                {
                    "category": "secret_leak",
                    "severity": "critical",
                    "title": "Credential material added",
                    "recommendation": "Remove the secret, rotate it, and load credentials from a secret store.",
                    "confidence": 0.99,
                },
            )
            findings.append(_finding(secret_rule, path, line_number, content))
    return findings


def _has_test_changes(files: list[dict[str, Any]]) -> bool:
    return any(TEST_PATH_PATTERN.search(str(item.get("new_path") or item.get("old_path") or "")) for item in files)


def _first_added_line(review_file: dict[str, Any]) -> int | None:
    for line_number, content, _ in _iter_added_lines(review_file):
        stripped = content.strip()
        if stripped and not stripped.startswith(("#", "import ", "from ")):
            if BEHAVIOR_PATTERN.search(content):
                return line_number
    return None


def _missing_test_findings(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _has_test_changes(files):
        return []
    rule = Rule(
        "tests.missing",
        re.compile(""),
        {
            "category": "missing_test",
            "severity": "medium",
            "title": "Production change has no test update",
            "recommendation": "Add a focused test covering the changed behavior and failure path.",
            "confidence": 0.76,
        },
    )
    findings = []
    for review_file in files:
        path = str(review_file.get("new_path") or review_file.get("old_path") or "")
        if not path or TEST_PATH_PATTERN.search(path) or EXEMPT_PATH_PATTERN.search(path):
            continue
        line = _first_added_line(review_file)
        if line is not None:
            findings.append(_finding(rule, path, line, path))
    return findings


def _deduplicate(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, int | None, str], dict[str, Any]] = {}
    for finding in findings:
        key = (finding["file"], finding["line"], finding["category"])
        previous = selected.get(key)
        if previous is None or finding["confidence"] > previous["confidence"]:
            selected[key] = finding
    return sorted(
        selected.values(),
        key=lambda item: (item["file"], item["line"] or 0, item["category"]),
    )


def scan(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic findings for one canonical review input."""
    files = payload["files"]
    findings = []
    for review_file in files:
        if not review_file.get("is_binary"):
            findings.extend(_scan_line_rules(review_file))
    findings.extend(_missing_test_findings(files))
    return _deduplicate(findings)[:MAX_OUTPUT_LINES]


def _write_findings(path: Path, findings: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as output:
        for finding in findings:
            output.write(json.dumps(finding, ensure_ascii=False, sort_keys=True))
            output.write("\n")


def parse_args() -> argparse.Namespace:
    """Parse fixed scanner arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    """Run scanner and write JSONL output."""
    args = parse_args()
    findings = scan(_load_input(args.input))
    _write_findings(args.output, findings)
    print(f"findings_written={len(findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
