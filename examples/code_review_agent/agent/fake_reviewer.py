# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Deterministic fake reviewer for code review dry-run tests."""

from __future__ import annotations

from .rules import review_with_rules
from .schemas import ParsedDiff
from .schemas import ReviewFinding


def review_with_fake_model(parsed_diff: ParsedDiff) -> list[ReviewFinding]:
    """Return deterministic findings for added lines in a parsed diff."""
    return review_with_rules(parsed_diff)
