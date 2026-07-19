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
    """Detect/normalize the script language.

    Explicit ``language`` wins. Without it, shebang is authoritative; otherwise
    a conservative heuristic is used. Mixed python+shell blobs are classified
    as the dominant signal, but :class:`SafetyScanner` dual-scans bash when
    python is selected and shell danger lines are present (fail-closed).
    """
    lang = (scan_input.language or "").strip().lower()
    if lang in ("python", "py"):
        return "python"
    if lang in ("bash", "sh", "shell"):
        return "bash"
    text = scan_input.script or ""
    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    if first_line.startswith("#!"):
        if "python" in first_line:
            return "python"
        if "bash" in first_line or re.search(r"\bsh\b", first_line):
            return "bash"
    # Prefer bash when clear shell command lines dominate, even if a python
    # keyword appears inside a string/echo (e.g. ``echo "import os"; rm -rf /``).
    if _has_leading_shell_commands(text) and not _looks_like_primary_python(text):
        return "bash"
    if _looks_like_primary_python(text):
        return "python"
    return "bash"


def _looks_like_primary_python(text: str) -> bool:
    """True when the script looks primarily like Python source."""
    if not text or not text.strip():
        return False
    # Structural python cues (not bare keywords that often appear in shell echo).
    if re.search(r"(?m)^\s*(def |class |async def |import |from \w+ import )", text):
        return True
    if re.search(r"(?m)^\s*print\(", text):
        return True
    return False


_SHELL_LEAD_RE = re.compile(
    r"(?m)^\s*(rm|curl|wget|sudo|su|doas|chmod|chown|pip|pip3|npm|ssh|scp|"
    r"git|nc|ncat|netcat|socat|telnet|apt|yum|dnf|brew|busybox|shred|unlink|"
    r"find|xargs|bash|sh|zsh)\b",
    re.IGNORECASE,
)


def _has_leading_shell_commands(text: str) -> bool:
    """True when any logical line starts with a common shell command."""
    return bool(_SHELL_LEAD_RE.search(text or ""))


def has_shell_command_lines(text: str) -> bool:
    """Public helper: script contains shell-looking command lines."""
    return _has_leading_shell_commands(text)


def parse_python_ast(script: str) -> ast.AST | None:
    """Best-effort parse; returns None on syntax errors."""
    try:
        return ast.parse(script)
    except SyntaxError:
        return None


def build_import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map local names to fully-qualified module/symbol paths.

    Examples::

        import os as x            -> {"x": "os"}
        from os import system     -> {"system": "os.system"}
        import requests           -> {"requests": "requests"}
        import http.client        -> {"http": "http"}  (top-level binding)
        import http.client as hc  -> {"hc": "http.client"}
        from pathlib import Path  -> {"Path": "pathlib.Path"}

    Note: bare ``import http.client`` only binds the name ``http`` to the
    ``http`` package (Python import semantics). Storing ``http → http.client``
    would double the middle segment when resolving
    ``http.client.HTTPSConnection`` → ``http.client.client.HTTPSConnection``.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    # import http.client as hc → hc refers to http.client
                    aliases[alias.asname] = alias.name
                else:
                    # import http.client → only 'http' is bound, to package 'http'
                    top = alias.name.split(".")[0]
                    aliases[top] = top
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
        # Avoid double-prefix when alias already ends with the next segment
        # (defensive; with correct build_import_aliases this is a no-op for
        # ``import http.client`` because aliases['http'] == 'http').
        rest = parts[1:]
        if rest and resolved_head.endswith("." + rest[0]):
            return resolved_head + "." + ".".join(rest[1:]) if len(rest) > 1 else resolved_head
        if rest and resolved_head == rest[0]:
            return resolved_head + "." + ".".join(rest[1:]) if len(rest) > 1 else resolved_head
        return resolved_head + "." + ".".join(rest)
    return raw


def iter_python_calls(tree: ast.AST, aliases: dict[str, str] | None = None) -> Iterable[tuple[ast.Call, str]]:
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
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, )):
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


def _has_line_continuation(raw: str) -> bool:
    """True when *raw* ends with an unescaped trailing backslash.

    Shell line-continuation requires a backslash as the final character of the
    physical line (spaces after ``\\`` break it). An even number of trailing
    backslashes means the last one is escaped and does **not** continue.
    """
    if not raw.endswith("\\"):
        return False
    n = 0
    for ch in reversed(raw):
        if ch == "\\":
            n += 1
        else:
            break
    return n % 2 == 1


def bash_lines(script: str) -> Iterable[tuple[int, str]]:
    """Yield (1-based line number, stripped logical line) for non-empty bash lines.

    Physical lines that end with an unescaped trailing backslash are merged
    with the following line(s) into one logical line so patterns such as::

        rm \\
        -rf \\
        /

    are visible to rule matchers as ``rm -rf /``. The reported line number is
    that of the first physical line in the continuation group. Comment-only
    and empty logical lines are skipped.
    """
    physical = script.splitlines()
    i = 0
    while i < len(physical):
        start_lineno = i + 1
        chunks: list[str] = [physical[i]]
        while _has_line_continuation(chunks[-1]) and i + 1 < len(physical):
            # Drop the trailing continuation backslash, keep the rest.
            chunks[-1] = chunks[-1][:-1]
            i += 1
            chunks.append(physical[i])
        i += 1
        # Shell deletes backslash+newline without inserting a space. Join the
        # same way so mid-token continuations (``r\\\nm -rf /`` → ``rm -rf /``)
        # reassemble correctly. Do NOT strip individual chunks before join —
        # trailing whitespace before ``\\`` is significant for token boundaries
        # (``rm \\\n-rf`` keeps the space → ``rm -rf``).
        logical = "".join(chunks).strip()
        if not logical or logical.startswith("#"):
            continue
        yield start_lineno, logical


def evidence_snippet(text: str, max_len: int = 120) -> str:
    """Trim a snippet for inclusion in a finding, collapsing whitespace."""
    snippet = " ".join((text or "").split())
    if len(snippet) > max_len:
        snippet = snippet[:max_len - 3] + "..."
    return snippet


def extract_inline_payloads(script: str) -> list[tuple[str, str]]:
    """Extract (language, payload) pairs from python/bash -c style invocations.

    Handles escaped quotes inside double/single-quoted payloads, e.g.::

        python -c "import os; os.system(\\"rm -rf /\\")"
    """
    payloads: list[tuple[str, str]] = []
    patterns = (
        (r"\bpython3?\s+-c\s+", "python"),
        (r"\b(?:bash|sh|zsh)\s+-c\s+", "bash"),
    )
    for prefix_re, lang in patterns:
        for m in re.finditer(prefix_re, script, flags=re.IGNORECASE):
            payload = _read_quoted_or_token(script, m.end())
            if payload:
                payloads.append((lang, payload))
    return payloads


def _read_quoted_or_token(text: str, start: int) -> str:
    """Read a shell-quoted string starting at *start*, honoring backslash escapes."""
    n = len(text)
    i = start
    while i < n and text[i].isspace():
        i += 1
    if i >= n:
        return ""
    quote = text[i]
    if quote in ("'", '"'):
        i += 1
        buf: list[str] = []
        while i < n:
            ch = text[i]
            if ch == "\\" and i + 1 < n:
                # Keep escape semantics for the inner language: store unescaped char.
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                return "".join(buf)
            buf.append(ch)
            i += 1
        return "".join(buf)
    # Unquoted token until whitespace / shell metachar.
    j = i
    while j < n and not text[j].isspace() and text[j] not in ";&|":
        j += 1
    return text[i:j]


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
