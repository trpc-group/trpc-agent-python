# Tencent is pleased to support the open source community by making trpc-agent-python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# trpc-agent-python is licensed under the Apache License Version 2.0.
"""Rule abstractions and shared parsing helpers.

A :class:`SafetyRule` receives a :class:`ScanInput` and a
:class:`PolicyConfig`, and returns a list of :class:`SafetyFinding`. Rules are
language-aware: each rule decides whether it applies to python, bash, or both.
"""
from __future__ import annotations

import ast
import re
import shlex
from abc import ABC
from abc import abstractmethod
from typing import Iterable

from ..policy import PolicyConfig
from ..types import RiskLevel
from ..types import SafetyFinding
from ..types import ScanInput


class SafetyRule(ABC):
    """Base class for all safety rules."""

    rule_id: str = "base"
    rule_name: str = "base rule"
    risk_type: str = "generic"
    default_level: RiskLevel = RiskLevel.MEDIUM
    languages: tuple[str, ...] = ("python", "bash")

    @abstractmethod
    def check(self, scan_input: ScanInput, policy: PolicyConfig) -> list[SafetyFinding]:
        """Return findings for this rule. Empty list when nothing matches."""

    def applies(self, language: str) -> bool:
        """True when this rule should run for *language*."""
        return language in self.languages


# ---------------------------------------------------------------------------
# Parsing helpers shared by multiple rules
# ---------------------------------------------------------------------------


def normalize_language(scan_input: ScanInput) -> str:
    """Detect/normalize the script language.

    Heuristics:
    - Explicit ScanInput.language wins when set to python/bash.
    - Otherwise infer from leading shebang or content shape.
    """
    lang = (scan_input.language or "").strip().lower()
    if lang in ("python", "bash", "sh"):
        return "python" if lang == "python" else "bash"
    text = scan_input.script or ""
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    if first_line.startswith("#!"):
        if "python" in first_line:
            return "python"
        if "bash" in first_line or "sh" in first_line:
            return "bash"
    # Fallback: presence of python keywords => python, else bash.
    if re.search(r"\b(def |import |from |print\(|class )", text):
        return "python"
    return "bash"


def parse_python_ast(script: str) -> ast.AST | None:
    """Best-effort parse; returns None on syntax errors."""
    try:
        return ast.parse(script)
    except SyntaxError:
        return None


def iter_python_calls(tree: ast.AST) -> Iterable[tuple[ast.Call, str]]:
    """Yield (call_node, dotted_name) for every Call in *tree*.

    dotted_name is the fully qualified function path when statically
    resolvable (e.g. ``os.system``), else ``"<expr>"``.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node, _dotted_name(node.func)


def _dotted_name(node: ast.AST) -> str:
    """Reconstruct a dotted attribute/name path from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return "<expr>"


def get_string_literal(node: ast.AST) -> str | None:
    """Return the string value of *node* when it is a constant string."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def bash_tokens(command: str) -> list[str]:
    """Tokenize a bash command. Falls back to whitespace split on error."""
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|;&<>()")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.split()


def bash_lines(script: str) -> Iterable[tuple[int, str]]:
    """Yield (1-based line number, stripped line) for non-empty bash lines."""
    for idx, raw in enumerate(script.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        yield idx, line


def evidence_snippet(text: str, max_len: int = 120) -> str:
    """Trim a snippet for inclusion in a finding, collapsing whitespace."""
    snippet = " ".join((text or "").split())
    if len(snippet) > max_len:
        snippet = snippet[:max_len - 3] + "..."
    return snippet
