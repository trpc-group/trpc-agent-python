# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic review rules for normalized diff input."""

from __future__ import annotations

import ast
import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass

from .models import ChangedFile
from .models import ChangedLine
from .models import Finding
from .models import FindingCategory
from .models import FindingSeverity
from .models import FindingSource
from .models import InputSummary
from .sanitizer import redact_text

_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b[A-Za-z0-9_.-]*(?:api[_-]?key|secret|token|password|passwd|pwd)[A-Za-z0-9_.-]*\b"
    r"\s*[:=]\s*(?:\[REDACTED\]|['\"][^'\"]{4,}['\"]|"
    r"(?!(?:none|true|false|null|undefined)\b)[A-Za-z0-9_./+=:-]{4,})")
_SECRET_LITERAL_RE = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{8,}|pk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9_]{8,}|gho_[A-Za-z0-9_]{8,}|"
    r"github_pat_[A-Za-z0-9_]{8,}|xox[bp]-[A-Za-z0-9-]{8,}|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16})\b")
_EVAL_EXEC_RE = re.compile(r"\b(eval|exec)\s*\(")
_SHELL_TRUE_RE = re.compile(r"\bshell\s*=\s*True\b")
_ASYNC_RESOURCE_RE = re.compile(r"\b(?:httpx\.AsyncClient|aiohttp\.ClientSession|AsyncClient|ClientSession)\s*\(")
_FILE_OPEN_RE = re.compile(r"\bopen\s*\(")
_DB_CONNECT_RE = re.compile(r"\b(?:sqlite3\.connect|psycopg2\.connect|pymysql\.connect|connect)\s*\(")
_CLOSE_RE = re.compile(r"\b(?:close|aclose|commit|rollback)\s*\(")
_CONTEXT_RE = re.compile(r"\b(?:with|async\s+with)\b")


@dataclass(frozen=True)
class _AstSignal:
    """A normalized AST signal anchored to an added-line number."""

    kind: str
    line: int | None
    evidence: str


def run_review_rules(input_summary: InputSummary) -> list[Finding]:
    """Run deterministic rules over parsed changed lines."""
    findings: list[Finding] = []
    for changed_file in input_summary.changed_files:
        if changed_file.is_binary:
            continue
        added_lines = list(_iter_added_lines(changed_file))
        if changed_file.path.endswith(".py"):
            ast_findings, parsed = _scan_python_ast(changed_file, added_lines)
            findings.extend(ast_findings)
            if parsed:
                for line in added_lines:
                    findings.extend(_scan_added_line(changed_file, line, include_dynamic=False))
                continue
        for line in added_lines:
            findings.extend(_scan_added_line(changed_file, line))
        findings.extend(_scan_lifecycle_patterns(changed_file, added_lines))

    missing_test = _missing_tests_finding(input_summary)
    if missing_test is not None:
        findings.append(missing_test)
    return findings


def _scan_added_line(changed_file: ChangedFile, line: ChangedLine, *, include_dynamic: bool = True) -> list[Finding]:
    content = line.content
    findings: list[Finding] = []
    if _SECRET_ASSIGNMENT_RE.search(content) or _SECRET_LITERAL_RE.search(content):
        findings.append(
            _finding(
                severity=FindingSeverity.CRITICAL,
                category=FindingCategory.SECRET,
                file=changed_file.path,
                line=line.new_line,
                title="Hard-coded secret in changed code",
                evidence=content,
                recommendation="Move secrets to a managed secret store or environment configuration.",
                confidence=0.95,
            ))
    if include_dynamic and (_EVAL_EXEC_RE.search(content) or _SHELL_TRUE_RE.search(content)):
        findings.append(
            _finding(
                severity=FindingSeverity.HIGH,
                category=FindingCategory.SECURITY,
                file=changed_file.path,
                line=line.new_line,
                title="Dynamic execution or shell invocation introduced",
                evidence=content,
                recommendation="Avoid dynamic execution and shell=True; pass validated arguments as a list.",
                confidence=0.9,
            ))
    return findings


def _scan_python_ast(changed_file: ChangedFile, lines: list[ChangedLine]) -> tuple[list[Finding], bool]:
    signals, parsed = _analyze_python_added_lines([(line.new_line, line.content) for line in lines])
    findings: list[Finding] = []
    for signal in signals:
        if signal.kind in {"dynamic_execution", "shell_true"}:
            findings.append(
                _finding(
                    severity=FindingSeverity.HIGH,
                    category=FindingCategory.SECURITY,
                    file=changed_file.path,
                    line=signal.line,
                    title="Dynamic execution or shell invocation introduced",
                    evidence=signal.evidence,
                    recommendation="Avoid dynamic execution and shell=True; pass validated arguments as a list.",
                    confidence=0.9,
                ))
        elif signal.kind == "async_client_unclosed":
            findings.append(
                _finding(
                    severity=FindingSeverity.MEDIUM,
                    category=FindingCategory.ASYNC,
                    file=changed_file.path,
                    line=signal.line,
                    title="Async client/session may not be closed",
                    evidence=signal.evidence,
                    recommendation="Use async with or close the async resource in a finally block.",
                    confidence=0.72,
                ))
        elif signal.kind == "file_open_unclosed":
            findings.append(
                _finding(
                    severity=FindingSeverity.MEDIUM,
                    category=FindingCategory.RESOURCE,
                    file=changed_file.path,
                    line=signal.line,
                    title="File handle may not be closed",
                    evidence=signal.evidence,
                    recommendation="Use a with statement or close the file handle in a finally block.",
                    confidence=0.7,
                ))
        elif signal.kind == "db_connect_unclosed":
            findings.append(
                _finding(
                    severity=FindingSeverity.MEDIUM,
                    category=FindingCategory.DB,
                    file=changed_file.path,
                    line=signal.line,
                    title="Database connection lifecycle is not bounded",
                    evidence=signal.evidence,
                    recommendation="Use a context manager and ensure commit/rollback/close on all paths.",
                    confidence=0.72,
                ))
    return findings, parsed


def _scan_lifecycle_patterns(changed_file: ChangedFile, lines: list[ChangedLine]) -> list[Finding]:
    findings: list[Finding] = []
    added_text = "\n".join(line.content for line in lines)
    if _ASYNC_RESOURCE_RE.search(added_text) and not _has_close_or_context(added_text):
        findings.append(
            _finding(
                severity=FindingSeverity.MEDIUM,
                category=FindingCategory.ASYNC,
                file=changed_file.path,
                line=_first_matching_line(lines, _ASYNC_RESOURCE_RE),
                title="Async client/session may not be closed",
                evidence=_first_matching_content(lines, _ASYNC_RESOURCE_RE),
                recommendation="Use async with or close the async resource in a finally block.",
                confidence=0.72,
            ))
    if _FILE_OPEN_RE.search(added_text) and not _has_close_or_context(added_text):
        findings.append(
            _finding(
                severity=FindingSeverity.MEDIUM,
                category=FindingCategory.RESOURCE,
                file=changed_file.path,
                line=_first_matching_line(lines, _FILE_OPEN_RE),
                title="File handle may not be closed",
                evidence=_first_matching_content(lines, _FILE_OPEN_RE),
                recommendation="Use a with statement or close the file handle in a finally block.",
                confidence=0.7,
            ))
    if _DB_CONNECT_RE.search(added_text) and not _has_close_or_context(added_text):
        findings.append(
            _finding(
                severity=FindingSeverity.MEDIUM,
                category=FindingCategory.DB,
                file=changed_file.path,
                line=_first_matching_line(lines, _DB_CONNECT_RE),
                title="Database connection lifecycle is not bounded",
                evidence=_first_matching_content(lines, _DB_CONNECT_RE),
                recommendation="Use a context manager and ensure commit/rollback/close on all paths.",
                confidence=0.72,
            ))
    return findings


def _has_close_or_context(text: str) -> bool:
    return bool(_CLOSE_RE.search(text) or _CONTEXT_RE.search(text))


def _analyze_python_added_lines(lines: list[tuple[int | None, str]]) -> tuple[list[_AstSignal], bool]:
    if not lines:
        return [], True
    line_numbers = [line_no for line_no, _content in lines if line_no is not None]
    base_line = min(line_numbers) if line_numbers else 1
    source = "\n".join(content for _line_no, content in lines)
    try:
        tree = ast.parse(source or "pass")
    except SyntaxError:
        return [], False
    _attach_parents(tree)
    line_lookup = {index: line_no for index, (line_no, _content) in enumerate(lines, start=1)}
    context_managed = _context_managed_calls(tree)
    closed_names = _closed_names(tree)
    signals: list[_AstSignal] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        line = line_lookup.get(getattr(node, "lineno", 0), base_line + getattr(node, "lineno", 1) - 1)
        evidence = _line_content(lines, line)
        if name in {"eval", "exec"}:
            signals.append(_AstSignal("dynamic_execution", line, evidence))
        elif name.startswith("subprocess.") and _has_shell_true(node):
            signals.append(_AstSignal("shell_true", line, evidence))
        elif name == "open" and id(node) not in context_managed and not _assigned_name_closed(node, closed_names):
            signals.append(_AstSignal("file_open_unclosed", line, evidence))
        elif _is_db_connect(name) and id(node) not in context_managed and not _assigned_name_closed(node, closed_names):
            signals.append(_AstSignal("db_connect_unclosed", line, evidence))
        elif _is_async_client(name) and id(node) not in context_managed and not _assigned_name_closed(
                node, closed_names):
            signals.append(_AstSignal("async_client_unclosed", line, evidence))
    return signals, True


def _context_managed_calls(tree: ast.AST) -> set[int]:
    calls: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if isinstance(item.context_expr, ast.Call):
                    calls.add(id(item.context_expr))
    return calls


def _closed_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in {"close", "aclose", "commit", "rollback"}:
                if isinstance(func.value, ast.Name):
                    names.add(func.value.id)
    return names


def _assigned_name_closed(call: ast.Call, closed_names: set[str]) -> bool:
    parent = getattr(call, "_parent", None)
    if isinstance(parent, ast.Assign):
        for target in parent.targets:
            if isinstance(target, ast.Name) and target.id in closed_names:
                return True
    return False


def _attach_parents(root: ast.AST) -> None:
    for parent in ast.walk(root):
        for child in ast.iter_child_nodes(parent):
            setattr(child, "_parent", parent)


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = [func.attr]
        value = func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr)
            value = value.value
        if isinstance(value, ast.Name):
            parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


def _has_shell_true(node: ast.Call) -> bool:
    return any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
               for keyword in node.keywords)


def _is_db_connect(name: str) -> bool:
    return name in {"sqlite3.connect", "psycopg2.connect", "pymysql.connect", "connect"} or name.endswith(".connect")


def _is_async_client(name: str) -> bool:
    return name in {"httpx.AsyncClient", "aiohttp.ClientSession", "AsyncClient", "ClientSession"}


def _line_content(lines: list[tuple[int | None, str]], line: int | None) -> str:
    for line_no, content in lines:
        if line_no == line:
            return content
    return ""


def _missing_tests_finding(input_summary: InputSummary) -> Finding | None:
    source_files = [item for item in input_summary.changed_files if _is_source_file(item.path)]
    if not source_files:
        return None
    test_paths = [item.path for item in input_summary.changed_files if _is_test_file(item.path)]
    uncovered = [
        item for item in source_files
        if not any(_is_related_test_path(item.path, test_path) for test_path in test_paths)
    ]
    if not uncovered:
        return None
    first = uncovered[0]
    return _finding(
        severity=FindingSeverity.LOW,
        category=FindingCategory.TEST,
        file=first.path,
        line=first.candidate_lines[0] if first.candidate_lines else None,
        title="Source change has no matching test update",
        evidence=(f"{len(uncovered)} of {len(source_files)} source file(s) changed without a matching test "
                  "or fixture change."),
        recommendation="Add or update focused tests or fixtures for the changed behavior.",
        confidence=0.65,
    )


def _iter_added_lines(changed_file: ChangedFile) -> Iterable[ChangedLine]:
    for hunk in changed_file.hunks:
        for line in hunk.lines:
            if line.line_type == "added":
                yield line


def _first_matching_line(lines: list[ChangedLine], pattern: re.Pattern[str]) -> int | None:
    for line in lines:
        if pattern.search(line.content):
            return line.new_line
    return None


def _first_matching_content(lines: list[ChangedLine], pattern: re.Pattern[str]) -> str:
    for line in lines:
        if pattern.search(line.content):
            return line.content
    return ""


def _is_source_file(path: str) -> bool:
    if _is_test_file(path):
        return False
    return path.endswith((".py", ".js", ".ts", ".tsx", ".go", ".java", ".kt", ".rs", ".rb", ".php"))


def _is_test_file(path: str) -> bool:
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    return ("/tests/" in f"/{lower}" or name.startswith("test_") or name.endswith("_test.py") or "fixture" in lower
            or lower.startswith("tests/"))


def _is_related_test_path(source_path: str, test_path: str) -> bool:
    source_parts = _path_parts(source_path)
    test_parts = _path_parts(test_path)
    if not source_parts or not test_parts or not _is_test_file(test_path):
        return False

    source_name = source_parts[-1]
    source_stem, source_suffix = _split_name(source_name)
    test_name = test_parts[-1]
    test_stem, _test_suffix = _split_name(test_name)
    source_parent = source_parts[:-1]
    test_parent = _strip_test_roots(test_parts[:-1])

    if "fixture" in "/".join(test_parts):
        return _fixture_matches(source_stem, test_stem, test_parent, source_parent)

    expected_names = _expected_test_names(source_stem, source_suffix)
    if test_name not in expected_names:
        return False
    return test_parent == source_parent or not test_parent


def _path_parts(path: str) -> tuple[str, ...]:
    return tuple(part for part in path.lower().replace("\\", "/").split("/") if part)


def _split_name(name: str) -> tuple[str, str]:
    if "." not in name:
        return name, ""
    stem, suffix = name.rsplit(".", 1)
    return stem, f".{suffix}"


def _strip_test_roots(parts: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(part for part in parts if part not in {"test", "tests", "__tests__", "fixtures", "fixture"})


def _expected_test_names(stem: str, suffix: str) -> set[str]:
    if suffix in {".js", ".jsx", ".ts", ".tsx"}:
        return {
            f"{stem}.test{suffix}",
            f"{stem}.spec{suffix}",
            f"test_{stem}{suffix}",
        }
    return {
        f"test_{stem}{suffix}",
        f"{stem}_test{suffix}",
    }


def _fixture_matches(
    source_stem: str,
    fixture_stem: str,
    fixture_parent: tuple[str, ...],
    source_parent: tuple[str, ...],
) -> bool:
    if fixture_parent and fixture_parent != source_parent:
        return False
    tokens = {token for token in re.split(r"[^a-z0-9]+", fixture_stem) if token}
    return fixture_stem == source_stem or source_stem in tokens


def _finding(
    *,
    severity: FindingSeverity,
    category: FindingCategory,
    file: str,
    line: int | None,
    title: str,
    evidence: str,
    recommendation: str,
    confidence: float,
) -> Finding:
    redacted_evidence = redact_text(evidence)
    redacted_recommendation = redact_text(recommendation)
    fingerprint = _fingerprint(category, file, line, title, redacted_evidence)
    return Finding(
        severity=severity,
        category=category,
        file=file,
        line=line,
        title=title,
        evidence=redacted_evidence,
        recommendation=redacted_recommendation,
        confidence=confidence,
        source=FindingSource.RULE,
        fingerprint=fingerprint,
    )


def _fingerprint(category: FindingCategory, file: str, line: int | None, title: str, evidence: str) -> str:
    raw = f"{category.value}|{file}|{line or ''}|{title}|{evidence}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
