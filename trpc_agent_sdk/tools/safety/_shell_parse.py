# Copyright (C) 2026 Tencent. All rights reserved.
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Quote-aware helpers for bash scanning.

We avoid a full POSIX shell parser (KISS). shlex tokenizes; a small quote
state machine distinguishes a real pipe/redirect from one inside a quoted
string so that `echo "a|b"` is not mis-flagged as a pipeline.
"""
from __future__ import annotations

import shlex


def split_tokens(cmd: str) -> list[str]:
    """Tokenize a command line (best-effort)."""
    try:
        return shlex.split(cmd, posix=True)
    except ValueError:
        # Unbalanced quotes etc. Fall back to whitespace split.
        return cmd.split()


def _iter_unquoted(cmd: str):
    """Yield (char, in_quote) walking the string with a quote state machine.

    Where in_quote is True for chars inside quotes or the quote chars themselves.
    """
    in_quote: Optional[str] = None
    for ch in cmd:
        if ch in ("'", '"'):
            if in_quote is None:
                in_quote = ch
            elif in_quote == ch:
                in_quote = None
            yield ch, True
            continue
        yield ch, in_quote is not None


def _has_unquoted(cmd: str, targets: set[str]) -> bool:
    chars = [ch for ch, in_q in _iter_unquoted(cmd) if not in_q]
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch in targets:
            # Distinguish single '&' from '&&', and single '|' from '||'
            if ch == "&" and i + 1 < len(chars) and chars[i + 1] == "&":
                i += 2  # Skip both &
                continue
            if ch == "|" and i + 1 < len(chars) and chars[i + 1] == "|":
                i += 2  # Skip both |
                continue
            return True
        i += 1
    return False


def has_pipeline(cmd: str) -> bool:
    return _has_unquoted(cmd, {"|"})


def has_redirection(cmd: str) -> bool:
    return _has_unquoted(cmd, {">", "<"})


def has_background(cmd: str) -> bool:
    """Detect if command has a background operator (unquoted single & not part of &&)."""
    chars = [ch for ch, in_q in _iter_unquoted(cmd) if not in_q]
    i = 0
    while i < len(chars):
        ch = chars[i]
        if ch == "&":
            # Check if this is part of && (logical AND)
            if i + 1 < len(chars) and chars[i + 1] == "&":
                i += 2  # Skip both &
                continue
            # This is a single unquoted & -> background operator
            return True
        i += 1
    return False


def first_command(cmd: str) -> str:
    tokens = split_tokens(cmd)
    if not tokens:
        return ""
    head = tokens[0]
    # Strip directory prefix: /usr/bin/curl -> curl
    return head.rsplit("/", 1)[-1]


def has_shell_bypass(cmd: str) -> bool:
    """Detect ways to hand a string to a fresh shell interpreter."""
    toks = split_tokens(cmd)
    if any(tok in ("sh", "bash", "zsh", "dash") for tok in toks):
        # only when followed by -c
        for i, t in enumerate(toks):
            if t in ("sh", "bash", "zsh", "dash") and i + 1 < len(toks) and toks[i + 1] == "-c":
                return True
    if "$(" in cmd or "`" in cmd:
        return True
    return False
