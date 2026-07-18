# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Shared AST / shell parsing helpers with import-alias resolution."""
from __future__ import annotations

import ast
import re
import shlex
from typing import Iterable

from ._types import ScanInput


def normalize_language(scan_input: ScanInput) -> str:
    """Detect/normalize the script language."""
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
    if re.search(r"\b(def |import |from |print\(|class )", text):
        return "python"
    return "bash"


def parse_python_ast(script: str) -> ast.AST | None:
    """Best-effort parse; returns None on syntax errors."""
    try:
        return ast.parse(script)
    except SyntaxError:
        return None


def build_import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map local names to fully-qualified module/symbol paths.

    Examples::

        import os as x          -> {"x": "os"}
        from os import system   -> {"system": "os.system"}
        import requests         -> {"requests": "requests"}
        from pathlib import Path -> {"Path": "pathlib.Path"}
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                # Keep the full imported module path for the bound name.
                aliases[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if not node.module:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or alias.name
                aliases[local] = f"{node.module}.{alias.name}"
    return aliases


def resolve_name(node: ast.AST, aliases: dict[str, str]) -> str:
    """Reconstruct a dotted call path, expanding import aliases."""
    raw = _dotted_name(node)
    if raw == "<expr>":
        return raw
    parts = raw.split(".")
    head = parts[0]
    if head in aliases:
        resolved_head = aliases[head]
        if len(parts) == 1:
            return resolved_head
        return resolved_head + "." + ".".join(parts[1:])
    return raw


def iter_python_calls(tree: ast.AST, aliases: dict[str, str] | None = None
                      ) -> Iterable[tuple[ast.Call, str]]:
    """Yield (call_node, resolved_dotted_name) for every Call in *tree*."""
    aliases = aliases if aliases is not None else build_import_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node, resolve_name(node.func, aliases)


def _dotted_name(node: ast.AST) -> str:
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


def collect_string_parts(node: ast.AST) -> list[str]:
    """Collect constant string fragments from BinOp joins / f-strings / calls."""
    parts: list[str] = []
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        parts.append(node.value)
    elif isinstance(node, ast.JoinedStr):
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add,)):
        parts.extend(collect_string_parts(node.left))
        parts.extend(collect_string_parts(node.right))
    elif isinstance(node, ast.Call):
        # Path.joinpath("a", "b") / os.path.join("a", "b")
        for arg in node.args:
            parts.extend(collect_string_parts(arg))
    elif isinstance(node, ast.Attribute):
        # Path.home().joinpath(...)  — walk value for nested calls
        parts.extend(collect_string_parts(node.value))
    return parts


def path_expr_text(node: ast.AST) -> str:
    """Best-effort flatten of a path-like expression into a searchable string."""
    parts = collect_string_parts(node)
    if parts:
        return "/".join(parts)
    s = get_string_literal(node)
    return s or ""


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


def extract_inline_payloads(script: str) -> list[tuple[str, str]]:
    """Extract (language, payload) pairs from python/bash -c style invocations."""
    payloads: list[tuple[str, str]] = []
    # python[3] -c '...'  /  python -c "..."
    for m in re.finditer(
        r"""\bpython3?\s+-c\s+(['"])(.*?)\1""",
        script,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        payloads.append(("python", m.group(2)))
    # bash/sh -c '...'
    for m in re.finditer(
        r"""\b(?:bash|sh|zsh)\s+-c\s+(['"])(.*?)\1""",
        script,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        payloads.append(("bash", m.group(2)))
    return payloads


def looks_like_url(value: str) -> bool:
    """Heuristic: string looks like a URL or host."""
    if not value:
        return False
    v = value.strip()
    if v.startswith(("http://", "https://", "ftp://")):
        return True
    if re.match(r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(/|:|$)", v):
        return True
    return False
