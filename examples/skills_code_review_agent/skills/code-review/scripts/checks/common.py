# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared contract for rule check modules.

Every ``check_*.py`` module in this package exposes::

    CATEGORY = "security"                       # one of CATEGORIES
    def run(files: list[FileCtx], mode: str, context: dict) -> list[dict]

``context`` carries optional extras: ``context["repo_context"]`` holds
``{"test_files": [...], "has_tests_dir": bool}`` in repo mode (empty dict in
diff-only mode).  Finding dicts must be built with :func:`make_finding` so the
schema stays uniform.  Checks must only use the Python standard library: they
execute inside the sandbox where no third-party packages are guaranteed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Optional

CATEGORIES = (
    "security",
    "secrets",
    "async",
    "resource_leak",
    "db_lifecycle",
    "missing_tests",
)

SEVERITIES = ("critical", "high", "medium", "low", "info")
CONFIDENCES = ("high", "medium", "low")
PRECISIONS = ("high", "low")

#: mode value when the host rebuilt full post-image files from a repository
MODE_REPO = "repo"
#: mode value when only the diff text was available (gap lines are blank)
MODE_DIFF_ONLY = "diff_only"

_EXT_LANG = {
    ".py": "python",
    ".pyw": "python",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "shell",
    ".bash": "shell",
    ".sql": "sql",
    ".go": "go",
    ".js": "javascript",
    ".ts": "typescript",
}


def language_for_path(path: str) -> str:
    """Guess the language from the file extension."""
    lower = path.lower()
    for ext, lang in _EXT_LANG.items():
        if lower.endswith(ext):
            return lang
    return "other"


@dataclass
class FileCtx:
    """One changed file as seen by the rule checks.

    ``content`` is the post-image text: the real file in repo mode, or a
    reconstruction from hunks in diff-only mode where unknown gap lines are
    blank so that line numbers still match the post-image.
    """

    path: str
    change_type: str = "modified"  # added|modified|deleted|renamed|binary
    old_path: Optional[str] = None
    language: str = "other"
    content: Optional[str] = None
    candidate_lines: set[int] = field(default_factory=set)
    content_complete: bool = True

    _lines: Optional[list[str]] = field(default=None, repr=False)
    _ast: object = field(default=None, repr=False)
    _ast_error: Optional[str] = field(default=None, repr=False)
    _ast_tried: bool = field(default=False, repr=False)

    @property
    def lines(self) -> list[str]:
        """Content split into lines; line N is ``lines[N-1]``."""
        if self._lines is None:
            self._lines = (self.content or "").splitlines()
        return self._lines

    def line_text(self, line: int) -> str:
        """Text of 1-based line number, empty string when out of range."""
        lines = self.lines
        if 1 <= line <= len(lines):
            return lines[line - 1]
        return ""

    def is_changed_line(self, line: int) -> bool:
        """True when the 1-based line is part of the change (added/modified)."""
        return line in self.candidate_lines

    def parse_ast(self):
        """Parse the content as Python, cached.  Returns (tree | None, error | None)."""
        if not self._ast_tried:
            self._ast_tried = True
            if self.language != "python" or self.content is None:
                self._ast_error = "not python"
            else:
                try:
                    self._ast = ast.parse(self.content)
                except SyntaxError as ex:
                    self._ast_error = f"syntax error: {ex.msg} (line {ex.lineno})"
        return self._ast, self._ast_error


def make_finding(*,
                 rule_id: str,
                 category: str,
                 severity: str,
                 file: str,
                 line: int,
                 title: str,
                 evidence: str,
                 recommendation: str,
                 confidence: str,
                 precision: str,
                 fix_snippet: Optional[dict] = None,
                 source: str = "static") -> dict:
    """Build one finding dict and validate enum fields early."""
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity: {severity}")
    if confidence not in CONFIDENCES:
        raise ValueError(f"unknown confidence: {confidence}")
    if precision not in PRECISIONS:
        raise ValueError(f"unknown precision: {precision}")
    if fix_snippet is not None and not ({"before", "after"} <= set(fix_snippet)):
        raise ValueError("fix_snippet requires 'before' and 'after'")
    return {
        "rule_id": rule_id,
        "category": category,
        "severity": severity,
        "precision": precision,
        "file": file,
        "line": int(line),
        "title": title,
        "evidence": evidence[:400],
        "recommendation": recommendation,
        "fix_snippet": fix_snippet,
        "confidence": confidence,
        "source": source,
    }


def call_name(node: ast.AST) -> str:
    """Dotted name of a Call's func, e.g. ``db.execute`` -> "db.execute".

    Returns "" for calls whose target is not a plain name/attribute chain.
    """
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def has_interpolation(node: ast.AST) -> bool:
    """True when the expression builds a string dynamically (f-string, +, %, .format)."""
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return has_string_side(node.left) or has_string_side(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format":
        return True
    return False


def has_string_side(node: ast.AST) -> bool:
    """True when the node is (or contains at top level) a string literal or f-string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return has_string_side(node.left) or has_string_side(node.right)
    return False
