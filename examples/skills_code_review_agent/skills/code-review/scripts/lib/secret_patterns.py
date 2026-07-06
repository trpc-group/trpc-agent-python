# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Canonical secret pattern table (stdlib-only, shared by sandbox and host).

This module is the single source of truth for secret detection: the sandbox
rule ``rule_secrets`` uses it to *detect* leaked credentials in diffs, and
the host-side ``codereview.redaction.SecretRedactor`` uses it to *scrub*
every string before it reaches the report or the database.
"""

from __future__ import annotations

import re
from typing import Any
from typing import Dict
from typing import List
from typing import Tuple

REDACTED_PLACEHOLDER = "***REDACTED***"

# Values that look like an assignment but are clearly not literal secrets.
_SAFE_VALUE_PREFIXES = (
    "os.environ",
    "os.getenv",
    "getenv(",
    "getpass",
    "environ[",
    "env(",
    "config[",
    "settings.",
    "none",
    "null",
    "true",
    "false",
    "${",
    "{{",
    "<",
    "***",
)

# Each entry: (pattern_id, regex, value_group)
#   value_group is the regex group holding the secret VALUE to redact
#   (0 means the whole match is the secret).
_RAW_PATTERNS: List[Tuple[str, str, int]] = [
    ("aws_access_key_id", r"\b((?:AKIA|ASIA|AGPA|AIDA|AROA|ANPA)[0-9A-Z]{16})\b", 1),
    (
        "aws_secret_access_key",
        r"(?i)\baws[_\-]?(?:secret[_\-]?(?:access[_\-]?)?key|secret)\b[^=:\n]{0,10}[=:]\s*[\"']?"
        r"([A-Za-z0-9/+=]{30,})",
        1,
    ),
    ("github_token", r"\b(gh[pousr]_[A-Za-z0-9]{20,})\b", 1),
    ("github_fine_grained_pat", r"\b(github_pat_[A-Za-z0-9_]{20,})\b", 1),
    ("gitlab_pat", r"\b(glpat-[A-Za-z0-9_\-]{16,})\b", 1),
    ("slack_token", r"\b(xox[baprs]-[A-Za-z0-9-]{10,})\b", 1),
    ("openai_api_key", r"\b(sk-[A-Za-z0-9_\-]{20,})\b", 1),
    ("google_api_key", r"\b(AIza[0-9A-Za-z_\-]{30,})\b", 1),
    ("jwt_token", r"\b(eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,})", 1),
    (
        "private_key_block",
        r"(-----BEGIN [A-Z ]*PRIVATE KEY-----(?:(?!-----END)[\s\S]){0,4096}(?:-----END [A-Z ]*PRIVATE KEY-----)?)",
        1,
    ),
    ("bearer_token", r"(?i)\bbearer\s+([A-Za-z0-9._~+/\-]{16,}=*)", 1),
    ("url_credentials", r"\b[a-z][a-z0-9+.\-]{1,16}://[^/\s:@\"']{1,64}:([^@/\s\"']{4,})@", 1),
    (
        "generic_assignment",
        # optional prefix segments allow FOO_TOKEN / "api-key" / aws.secret style keys
        r"(?i)\b(?:[A-Za-z][A-Za-z0-9]*[_\-])*"
        r"(?:password|passwd|pwd|secret|token|api[_\-]?key|apikey|access[_\-]?key|secret[_\-]?key|"
        r"account[_\-]?key|auth[_\-]?token|client[_\-]?secret|private[_\-]?key|"
        r"db[_\-]?pass(?:word)?|credentials?)\b"
        r"[\"']?\s*(?:=>|:=|[:=])\s*[\"']?([^\s\"',;)}\]]{6,})",
        1,
    ),
]

SECRET_PATTERNS: List[Tuple[str, "re.Pattern[str]", int]] = [
    (pattern_id, re.compile(pattern), group) for pattern_id, pattern, group in _RAW_PATTERNS
]


_RE_FUNCTION_CALL_VALUE = re.compile(r"^[A-Za-z_][\w.]*\(")


def _is_safe_value(value: str) -> bool:
    lowered = value.strip().lower()
    if any(lowered.startswith(prefix) for prefix in _SAFE_VALUE_PREFIXES):
        return True
    # Function-call values (get_secret(...), vault.read(...)) are references,
    # not literal secrets.
    return bool(_RE_FUNCTION_CALL_VALUE.match(value.strip()))


def find_secrets(text: str) -> List[Dict[str, Any]]:
    """Find secret value spans in ``text``.

    Returns a list of ``{"id", "start", "end"}`` dicts (never the secret
    value itself) with overlapping spans merged.
    """
    spans: List[Tuple[int, int, str]] = []
    for pattern_id, regex, group in SECRET_PATTERNS:
        for match in regex.finditer(text):
            start, end = match.span(group)
            if start == end:
                continue
            if pattern_id == "generic_assignment" and _is_safe_value(match.group(group)):
                continue
            spans.append((start, end, pattern_id))
    spans.sort(key=lambda s: (s[0], -s[1]))

    merged: List[Dict[str, Any]] = []
    for start, end, pattern_id in spans:
        if merged and start < merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], end)
            continue
        merged.append({"id": pattern_id, "start": start, "end": end})
    return merged


def contains_secret(text: str) -> bool:
    """Return True when ``text`` contains at least one detectable secret."""
    return bool(find_secrets(text))


def redact_text(text: str, placeholder: str = REDACTED_PLACEHOLDER) -> Tuple[str, int]:
    """Replace every detected secret value in ``text`` with ``placeholder``.

    Returns:
        (redacted_text, number_of_redacted_spans)
    """
    spans = find_secrets(text)
    if not spans:
        return text, 0
    pieces: List[str] = []
    cursor = 0
    for span in spans:
        pieces.append(text[cursor:span["start"]])
        pieces.append(placeholder)
        cursor = span["end"]
    pieces.append(text[cursor:])
    return "".join(pieces), len(spans)
