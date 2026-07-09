#!/usr/bin/env python3
# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Sensitive-information masking (Phase 2).

Replaces known secret formats and high-entropy tokens in arbitrary text with
``***REDACTED***``. Shared with the ``sensitive_info`` rule set so that
detection and masking stay in sync.

Regex set (known formats)
-------------------------
* ``AKIA[0-9A-Z]{16}``                 — AWS Access Key ID
* ``sk-[a-zA-Z0-9]{20,}``              — OpenAI API key
* ``ghp_[a-zA-Z0-9]{36}``              — GitHub personal access token
* ``password\\s*=\\s*["']...["']``      — plaintext password assignment
* ``-----BEGIN ... PRIVATE KEY-----``   — private key header
* ``(postgres|mysql|mongodb)://u:p@``   — connection string with credentials

Entropy detection
-----------------
Tokens of ≥20 base64-ish chars with Shannon entropy > 4.5 are treated as
suspected secrets (catches un-prefixed random keys).

Usage
-----
    from mask_secrets import mask_secrets
    clean, n = mask_secrets(text)

    # CLI:
    python mask_secrets.py < input.txt          # masked text to stdout, count to stderr
    python mask_secrets.py input.txt
"""

from __future__ import annotations

import math
import re
import sys

# Known secret formats. Order matters only for readability; each match is
# replaced before the next runs, so overlapping patterns are fine.
_KNOWN_PATTERNS: list[re.Pattern] = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[a-zA-Z0-9]{20,}"),
    re.compile(r"ghp_[a-zA-Z0-9]{36}"),
    re.compile(r"""password\s*=\s*["'][^"']+["']"""),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?:postgres|mysql|mongodb)://[^\s/]+:[^\s@/]+@"),
]

# Candidate tokens for entropy analysis: long runs of base64/url-safe chars.
_TOKEN_RE = re.compile(r"[A-Za-z0-9+/=_-]{20,}")

_REDACTED = "***REDACTED***"

# Entropy threshold per spec.
_ENTROPY_THRESHOLD = 4.5


def _shannon_entropy(s: str) -> float:
    """Shannon entropy (bits/char) of a string — 0 for empty."""
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((f / n) * math.log2(f / n) for f in freq.values())


def _mask_known(text: str) -> tuple[str, int]:
    """Replace all known-format secrets. Returns (masked_text, hit_count)."""
    count = 0
    masked = text
    for pat in _KNOWN_PATTERNS:
        masked, n = pat.subn(_REDACTED, masked)
        count += n
    return masked, count


def _mask_entropy(text: str) -> tuple[str, int]:
    """Replace high-entropy tokens (≥20 chars, entropy > 4.5)."""
    count = 0
    out: list[str] = []
    last = 0
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        if _shannon_entropy(tok) > _ENTROPY_THRESHOLD:
            out.append(text[last:m.start()])
            out.append(_REDACTED)
            last = m.end()
            count += 1
    out.append(text[last:])
    return "".join(out), count


def mask_secrets(text: str) -> tuple[str, int]:
    """Mask known secrets + high-entropy tokens.

    Returns ``(masked_text, hit_count)`` where hit_count is the total number
    of redactions (known formats + entropy hits). Known formats are masked
    first so their long random tails don't double-count via entropy.
    """
    if not text:
        return text, 0
    masked, n1 = _mask_known(text)
    masked, n2 = _mask_entropy(masked)
    return masked, n1 + n2


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _read_input(argv: list[str]) -> str:
    if len(argv) > 1 and argv[1] != "-":
        with open(argv[1], "r", encoding="utf-8") as fh:
            return fh.read()
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    text = _read_input(argv)
    masked, count = mask_secrets(text)
    sys.stdout.write(masked)
    sys.stderr.write(f"[mask_secrets] redacted {count} secret(s)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
