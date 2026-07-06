# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared helpers and data model for the review rule modules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Tuple

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_INFO = "info"

SOURCE_STATIC_RULE = "static_rule"

_RE_DEF = re.compile(r"^(\s*)(async\s+)?def\s+\w+")


@dataclass
class RuleContext:
    """Per-file context handed to every rule module."""

    file_entry: Dict[str, Any]
    content: Optional[str] = None
    content_lines: List[str] = field(default_factory=list)

    @property
    def path(self) -> str:
        return self.file_entry.get("path", "")


def make_finding(rule_id: str,
                 category: str,
                 severity: str,
                 confidence: float,
                 file: str,
                 line: int,
                 title: str,
                 evidence: str,
                 recommendation: str) -> Dict[str, Any]:
    """Build one finding dict with the exact schema required by the agent."""
    return {
        "severity": severity,
        "category": category,
        "file": file,
        "line": int(line or 0),
        "title": title,
        "evidence": evidence.strip(),
        "recommendation": recommendation,
        "confidence": round(float(confidence), 2),
        "source": SOURCE_STATIC_RULE,
        "rule_id": rule_id,
    }


def is_code_line(content: str) -> bool:
    """Skip blank lines and pure comments."""
    stripped = content.strip()
    return bool(stripped) and not stripped.startswith("#")


def iter_added_lines(file_entry: Dict[str, Any]) -> Iterator[Tuple[int, str, Dict[str, Any]]]:
    """Yield ``(new_lineno, content, hunk)`` for every added line of a file."""
    for hunk in file_entry.get("hunks", []):
        for line in hunk.get("lines", []):
            if line.get("tag") == "+":
                yield line.get("new_lineno") or 0, line.get("content", ""), hunk


def hunk_added_text(hunk: Dict[str, Any]) -> str:
    """All added-line contents of a hunk joined as one text block."""
    return "\n".join(line.get("content", "") for line in hunk.get("lines", []) if line.get("tag") == "+")


def file_added_text(file_entry: Dict[str, Any]) -> str:
    """All added-line contents of a file joined as one text block."""
    return "\n".join(content for _, content, _ in iter_added_lines(file_entry))


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip())


def _scan_defs_backwards(lines: List[Tuple[int, str]], target_indent: int) -> Optional[bool]:
    """Scan candidate lines bottom-up for the nearest enclosing ``def``.

    Args:
        lines: (index, content) pairs ordered top-down; scanned in reverse.
        target_indent: indent of the line whose enclosing function we seek.

    Returns:
        True/False when an enclosing def was found (async or not), else None.
    """
    for _, content in reversed(lines):
        if not content.strip():
            continue
        match = _RE_DEF.match(content)
        if match and _indent_of(content) < target_indent:
            return bool(match.group(2))
    return None


def enclosing_def_is_async(ctx: RuleContext, hunk: Dict[str, Any], new_lineno: int, line_content: str) -> bool:
    """Best-effort check whether ``new_lineno`` sits inside an ``async def``.

    Prefers the full new-file content when available (repo-path / file-list
    inputs); otherwise falls back to the visible hunk lines (context lines
    included), which is the best a pure diff can offer.
    """
    target_indent = _indent_of(line_content)
    if target_indent == 0:
        return False

    if ctx.content_lines and 0 < new_lineno <= len(ctx.content_lines):
        above = [(i, ctx.content_lines[i]) for i in range(new_lineno - 1)]
        result = _scan_defs_backwards(above, target_indent)
        if result is not None:
            return result

    visible: List[Tuple[int, str]] = []
    for line in hunk.get("lines", []):
        lineno = line.get("new_lineno")
        if line.get("tag") in ("+", " ") and lineno is not None and lineno < new_lineno:
            visible.append((lineno, line.get("content", "")))
    result = _scan_defs_backwards(visible, target_indent)
    return bool(result)
