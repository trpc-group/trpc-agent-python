# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Cross-backend normalization rules and summary-text helpers.

Two constants are exported for downstream tooling that needs to introspect the
framework's behavior:

- :data:`NORMALIZATION_RULES` – JSON-path/strategy pairs applied before any
  cross-backend comparison.
- :data:`ALLOWED_DIFF_RULES` – rules describing categories of differences that
  are considered acceptable for cross-backend replay correctness.
"""
from __future__ import annotations

import unicodedata
from typing import Optional

SUMMARY_PREFIX = "Previous conversation summary:"

NORMALIZATION_RULES = [
    {
        "path": "$.events[*].timestamp",
        "strategy": "replace_with_placeholder",
        "reason": "Wall-clock timestamps are non-business metadata.",
    },
    {
        "path": "$.events[*].id",
        "strategy": "logical_replay_id_or_stable_index",
        "reason": "Backends and summary generation may allocate different physical IDs.",
    },
    {
        "path": "$.summary.*.text",
        "strategy": "unicode_nfkc_casefold_and_whitespace_collapse",
        "reason": "Summary content is compared semantically for formatting-only differences.",
    },
    {
        "path": "$.memory.*",
        "strategy": "sort_by_normalized_content_author",
        "reason": "MemoryService does not define result ordering for equal keyword matches.",
    },
    {
        "path": "$.*",
        "strategy": "structural_json_comparison",
        "reason": "Serialized object key order is not business data.",
    },
]

ALLOWED_DIFF_RULES = [
    {
        "path": "$.memory.*",
        "scope": "order_only",
        "reason": "Keyword-memory ranking order is backend-specific; entry content and count must still match.",
    },
    {
        "path": "$.recovery_raw[*].mechanism",
        "scope": "mechanism_only",
        "reason": "A backend may reject a duplicate transactionally or require compensating cleanup.",
    },
]


def normalize_summary_text(text: Optional[str]) -> Optional[str]:
    """Normalize summary formatting while preserving words and punctuation."""
    if text is None:
        return None
    normalized = unicodedata.normalize("NFKC", text)
    return " ".join(normalized.split()).casefold()