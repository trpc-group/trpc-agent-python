# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Allowed-diff rule engine with JSONPath matching and governance limits.

Allowed diffs capture backend-specific variance that is expected and
non-semantic (e.g., backend name, timestamp presence).  This module
provides:

- AllowedDiffRule: a single rule with JSONPath pattern + mandatory reason
- is_allowed: check whether a field path matches any rule
- Governance: per-case limits on how many fields may be marked allowed
  (prevents using allowed_diff as a blanket to hide real inconsistencies)
"""

from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel
from pydantic import Field


class AllowedDiffRule(BaseModel):
    """A single rule for marking a field-level diff as allowed.

    The path field uses a JSONPath-like syntax:
    - Exact: "events[0].timestamp"
    - Wildcard index: "events[*].timestamp" matches any index
    - Wildcard suffix: "events[*]" matches the whole events array entry
    - Prefix match: "backend" matches all paths starting with "backend"

    Every rule must include a reason explaining why the diff is expected.
    """

    path: str = Field(..., description="JSONPath-like pattern for the field")
    reason: str = Field(..., description="Why this diff is allowed")
    backend_pair: Optional[tuple[str, str]] = Field(
        default=None, description="Optional backend pair scope restriction"
    )

    def matches(self, field_path: str) -> bool:
        """Check whether this rule matches the given field path.

        Args:
            field_path: A dot-separated path like "events[0].text".

        Returns:
            True if this rule's pattern matches the field path.
        """
        pattern = self._to_regex(self.path)
        return bool(pattern.match(field_path))

    @staticmethod
    def _to_regex(path: str) -> re.Pattern:
        """Convert a JSONPath-like pattern to a compiled regex.

        Uses re.escape for safe escaping of all regex metacharacters,
        with a wildcard placeholder for [*] index matching.
        """
        # Replace [*] with a unique marker before escaping
        _MARKER = "\x00WILDCARD\x00"
        working = path.replace("[*]", _MARKER)
        # Escape all regex metacharacters: . [ ] ( ) etc.
        escaped = re.escape(working)
        # Replace marker with \d+ inside brackets
        escaped = escaped.replace(_MARKER, r"\[\d+\]")
        # If pattern ends with wildcard index, allow optional suffix
        if escaped.endswith(r"\[\d+\]"):
            escaped += r"(\..*)?"
        return re.compile(f"^{escaped}$")


# ── Governance ────────────────────────────────────────────────────
# Per-case limits to prevent allowed_diff abuse.  These are enforced
# in test_allowed_diff_governance.py.

MAX_ALLOWED_PER_CASE = 8
"""Maximum number of allowed diffs per case."""

MAX_ALLOWED_RATIO = 0.10
"""Maximum ratio of allowed diffs to total comparison fields."""


def is_allowed(
    field_path: str,
    rules: tuple[AllowedDiffRule, ...],
) -> tuple[bool, str]:
    """Check whether a field path is covered by any allowed-diff rule.

    Args:
        field_path: The dot-separated field path to check.
        rules: The set of AllowedDiffRule to match against.

    Returns:
        A tuple of (is_allowed, reason).  If no rule matches, reason
        is an empty string.
    """
    for rule in rules:
        if rule.matches(field_path):
            return True, rule.reason
    return False, ""


def check_governance(
    total_fields: int,
    used_allowed: int,
) -> None:
    """Enforce governance limits on allowed diffs.

    Raises:
        AssertionError: If either the count or ratio limit is exceeded.
    """
    if used_allowed > MAX_ALLOWED_PER_CASE:
        raise AssertionError(
            f"Allowed diff count {used_allowed} exceeds per-case limit "
            f"of {MAX_ALLOWED_PER_CASE}"
        )
    if total_fields > 0:
        ratio = used_allowed / total_fields
        if ratio > MAX_ALLOWED_RATIO:
            raise AssertionError(
                f"Allowed diff ratio {ratio:.2%} exceeds per-case limit "
                f"of {MAX_ALLOWED_RATIO:.0%} ({used_allowed}/{total_fields})"
            )
