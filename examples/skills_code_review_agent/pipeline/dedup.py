# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Dedup + denoise (issue #92, requirement 6).

- Dedup: at most one finding per (file, line, category); keep the highest-confidence one and mark
  the rest ``status="duplicate"``.
- Denoise: route findings by confidence so low-confidence ones never mix into high-confidence
  actionable findings — ``active`` (>= warn) / ``warning`` (>= review) / ``needs_human_review``.
"""
from __future__ import annotations

from .types import Finding

# Confidence cutoffs (tunable policy). active >= WARN; warning in [REVIEW, WARN); else human-review.
WARN_THRESHOLD = 0.7
REVIEW_THRESHOLD = 0.4


def dedup_key(f: Finding) -> str:
    # File-level findings (line is None) share file+category but are distinct issues — key on the
    # rule/title too so two different file-level findings in one category don't collapse into one.
    if f.line is None:
        return f"{f.file}::{f.category}:{f.rule_id or f.title}"
    return f"{f.file}:{f.line}:{f.category}"


def _denoise_status(confidence: float) -> str:
    if confidence >= WARN_THRESHOLD:
        return "active"
    if confidence >= REVIEW_THRESHOLD:
        return "warning"
    return "needs_human_review"


def dedup_and_denoise(
    findings: list[Finding],
    warn_threshold: float = WARN_THRESHOLD,
    review_threshold: float = REVIEW_THRESHOLD,
) -> list[Finding]:
    """Return findings with ``dedup_key`` and ``status`` set. Duplicates are kept but marked."""
    best: dict[str, Finding] = {}
    order: list[str] = []
    dupes: list[Finding] = []

    for f in findings:
        key = dedup_key(f)
        f = f.model_copy(update={"dedup_key": key})
        if key not in best:
            best[key] = f
            order.append(key)
        elif f.confidence > best[key].confidence:
            dupes.append(best[key].model_copy(update={"status": "duplicate"}))
            best[key] = f
        else:
            dupes.append(f.model_copy(update={"status": "duplicate"}))

    result: list[Finding] = []
    for key in order:
        f = best[key]
        status = "active" if f.confidence >= warn_threshold else (
            "warning" if f.confidence >= review_threshold else "needs_human_review")
        result.append(f.model_copy(update={"status": status}))
    result.extend(dupes)
    return result
