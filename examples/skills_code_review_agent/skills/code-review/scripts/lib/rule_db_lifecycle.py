# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""DB transaction / connection lifecycle rules (category: db_lifecycle)."""

from __future__ import annotations

import re
from typing import Any
from typing import Dict
from typing import List

from .rulebase import RuleContext
from .rulebase import SEVERITY_HIGH
from .rulebase import SEVERITY_MEDIUM
from .rulebase import file_added_text
from .rulebase import is_code_line
from .rulebase import iter_added_lines
from .rulebase import make_finding

CATEGORY = "db_lifecycle"

_RE_CONNECT_ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*[\w.]+\.connect\s*\(")
_RE_WITH_CONNECT = re.compile(r"\bwith\s+[^:]*\.connect\s*\(")
_RE_CURSOR_ASSIGN = re.compile(r"^\s*(\w+)\s*=\s*\w+\.cursor\s*\(")
_RE_BEGIN = re.compile(r"(?:\.begin\s*\(|\bexecute\s*\(\s*[\"']\s*BEGIN\b)", re.IGNORECASE)
_RE_COMMIT_OR_ROLLBACK = re.compile(r"\.(commit|rollback)\s*\(|\bexecute\s*\(\s*[\"']\s*(COMMIT|ROLLBACK)\b",
                                    re.IGNORECASE)
_RE_AUTOCOMMIT_TRUE = re.compile(r"\bautocommit\s*=\s*True\b")
_RE_TXN_OP = re.compile(r"\.(commit|rollback|begin)\s*\(")
_RE_LOOP_HEADER = re.compile(r"^\s*(?:for|while)\b")


def _var_released(scope_text: str, var: str) -> bool:
    needle = re.compile(rf"\b{re.escape(var)}\s*\.\s*close\s*\(|\bwith\s+.*\b{re.escape(var)}\b")
    return bool(needle.search(scope_text))


def check_file(ctx: RuleContext) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    path = ctx.path
    scope_text = file_added_text(ctx.file_entry)
    if ctx.content:
        scope_text = scope_text + "\n" + ctx.content
    loop_depth_lines: List[int] = []

    for lineno, content, _hunk in iter_added_lines(ctx.file_entry):
        if not is_code_line(content):
            continue
        if _RE_LOOP_HEADER.match(content):
            loop_depth_lines.append(len(content) - len(content.lstrip()))
            continue

        match = _RE_CONNECT_ASSIGN.match(content)
        if match and not _RE_WITH_CONNECT.search(content):
            indent = len(content) - len(content.lstrip())
            in_loop = any(indent > loop_indent for loop_indent in loop_depth_lines)
            if in_loop:
                findings.append(
                    make_finding("DBL004", CATEGORY, SEVERITY_HIGH, 0.7, path, lineno,
                                 "Database connection created inside a loop", content,
                                 "Create the connection once outside the loop (or use a pool); "
                                 "per-iteration connections exhaust the server connection limit."))
            if not _var_released(scope_text, match.group(1)):
                findings.append(
                    make_finding("DBL001", CATEGORY, SEVERITY_HIGH, 0.75, path, lineno,
                                 "Database connection opened without close()", content,
                                 "Use `with ... .connect(...) as conn:` or close the connection "
                                 "in a `finally` block so it returns to the pool on every path."))
        match = _RE_CURSOR_ASSIGN.match(content)
        if match and "with" not in content and not _var_released(scope_text, match.group(1)):
            findings.append(
                make_finding("DBL002", CATEGORY, SEVERITY_MEDIUM, 0.6, path, lineno,
                             "Cursor created without close()", content,
                             "Use `with conn.cursor() as cur:` (or close it in finally); open "
                             "cursors hold server-side resources."))
        if _RE_BEGIN.search(content) and not _RE_COMMIT_OR_ROLLBACK.search(scope_text):
            findings.append(
                make_finding("DBL003", CATEGORY, SEVERITY_HIGH, 0.75, path, lineno,
                             "Transaction started without commit/rollback in change", content,
                             "Pair every BEGIN with commit() on success and rollback() in the "
                             "exception path — `with conn.begin():` does both automatically."))
        if _RE_AUTOCOMMIT_TRUE.search(content) and _RE_TXN_OP.search(scope_text):
            findings.append(
                make_finding("DBL005", CATEGORY, SEVERITY_MEDIUM, 0.6, path, lineno,
                             "autocommit=True mixed with explicit transaction calls", content,
                             "Pick one mode: either autocommit for single statements or explicit "
                             "begin/commit blocks; mixing both silently breaks atomicity."))
    return findings
