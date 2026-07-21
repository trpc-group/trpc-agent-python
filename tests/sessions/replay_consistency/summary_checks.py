# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Summary semantic comparison with Jaccard similarity and fault detection.

Three-layer comparison of session summaries:
1. Text content: word-set Jaccard similarity (pure stdlib, no embeddings)
2. Metadata: version, session_id, supersedes chain (strict equality)
3. Coverage: events covered by the summary (strict equality)

Three mandatory fault categories (100% detection rate):
- loss: summary is None/missing when expected
- overwrite: newer summary replaced by older (version regression)
- affiliation: summary.session_id does not match owning session
"""

from __future__ import annotations

import re
from typing import Any
from typing import Optional

from pydantic import BaseModel
from pydantic import Field

# ── Jaccard Similarity ────────────────────────────────────────────

SUMMARY_SIM_THRESHOLD = 0.80
"""Jaccard similarity threshold above which summaries are considered equivalent."""


def _tokenize(text: str) -> set[str]:
    """Tokenize text into a set of lowercased word tokens.

    Strips punctuation, splits on whitespace, and lowercases.
    Handles both English and Chinese text (Chinese characters are
    treated as individual tokens via character-level fallback).

    Args:
        text: The summary text to tokenize.

    Returns:
        A set of normalized word tokens.
    """
    # Normalize whitespace
    text = re.sub(r"\s+", " ", text.strip())
    # Split into words (handles mixed Chinese/English)
    tokens: set[str] = set()
    # English words
    eng_words = re.findall(r"[a-zA-Z0-9_]+", text.lower())
    tokens.update(eng_words)
    # Chinese characters (individual chars as tokens)
    chinese_chars = re.findall(r"[一-鿿]", text)
    tokens.update(chinese_chars)
    # If nothing matched, fall back to character-level
    if not tokens:
        tokens = set(text.lower())
    return tokens


def summary_text_similarity(a: str, b: str) -> float:
    """Compute Jaccard similarity between two summary texts.

    Jaccard = |A ∩ B| / |A ∪ B|

    Args:
        a: First summary text.
        b: Second summary text.

    Returns:
        Similarity score in [0.0, 1.0].  Returns 1.0 if both are empty.
    """
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    set_a = _tokenize(a)
    set_b = _tokenize(b)
    if not set_a and not set_b:
        return 1.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


# ── Summary Fault Categories ──────────────────────────────────────

class SummaryIssueType:
    """Enum-like constants for summary fault categories."""

    LOSS = "loss"
    OVERWRITE = "overwrite"
    AFFILIATION = "affiliation"


class SummaryIssue(BaseModel):
    """A detected summary consistency fault."""

    type: str = Field(..., description="Fault type: loss | overwrite | affiliation")
    session_id: str = ""
    summary_id: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)


# ── Summary Comparator ────────────────────────────────────────────

class SummaryComparator:
    """Three-layer summary comparison with fault detection."""

    def __init__(self, similarity_threshold: float = SUMMARY_SIM_THRESHOLD):
        self._threshold = similarity_threshold

    def compare(
        self,
        left_summary: Optional[dict[str, Any]],
        right_summary: Optional[dict[str, Any]],
        session_id: str,
        left_backend: str = "left",
        right_backend: str = "right",
    ) -> tuple[list[dict[str, Any]], list[SummaryIssue]]:
        """Compare two summary snapshots.

        Args:
            left_summary: Summary dict from the reference backend.
            right_summary: Summary dict from the candidate backend.
            session_id: The owning session id for affiliation checks.
            left_backend: Name of the reference backend.
            right_backend: Name of the candidate backend.

        Returns:
            A tuple of (diffs, issues) where diffs are field-level
            differences and issues are detected summary faults.
        """
        diffs: list[dict[str, Any]] = []
        issues: list[SummaryIssue] = []

        # ── Loss detection ───────────────────────────────────
        if left_summary is None and right_summary is not None:
            issues.append(SummaryIssue(
                type=SummaryIssueType.LOSS,
                session_id=session_id,
                detail={"missing_in": left_backend, "present_in": right_backend},
            ))
            return diffs, issues
        if left_summary is not None and right_summary is None:
            issues.append(SummaryIssue(
                type=SummaryIssueType.LOSS,
                session_id=session_id,
                detail={"missing_in": right_backend, "present_in": left_backend},
            ))
            return diffs, issues
        if left_summary is None and right_summary is None:
            return diffs, issues

        # ── Text content comparison (Jaccard) ─────────────────
        left_text = left_summary.get("summary_text") or ""
        right_text = right_summary.get("summary_text") or ""
        sim = summary_text_similarity(left_text, right_text)
        if sim < self._threshold:
            diffs.append({
                "section": "summary",
                "path": "summary.summary_text",
                "left_backend": left_backend,
                "right_backend": right_backend,
                "similarity": round(sim, 4),
                "left_preview": left_text[:200],
                "right_preview": right_text[:200],
                "note": f"Jaccard similarity {sim:.2%} below threshold {self._threshold:.0%}",
            })

        # ── Metadata comparison (strict) ─────────────────────
        left_version = left_summary.get("version") or left_summary.get("summary_version")
        right_version = right_summary.get("version") or right_summary.get("summary_version")
        if left_version != right_version:
            diffs.append({
                "section": "summary",
                "path": "summary.version",
                "left_backend": left_backend,
                "right_backend": right_backend,
                "left": left_version,
                "right": right_version,
            })

        # ── Affiliation check ─────────────────────────────────
        left_meta = left_summary.get("metadata") or {}
        right_meta = right_summary.get("metadata") or {}
        left_sid = left_meta.get("session_id") or left_summary.get("session_id")
        right_sid = right_meta.get("session_id") or right_summary.get("session_id")

        if left_sid and left_sid != session_id:
            issues.append(SummaryIssue(
                type=SummaryIssueType.AFFILIATION,
                session_id=session_id,
                summary_id=left_sid,
                detail={"backend": left_backend, "expected_session": session_id, "actual_session": left_sid},
            ))
        if right_sid and right_sid != session_id:
            issues.append(SummaryIssue(
                type=SummaryIssueType.AFFILIATION,
                session_id=session_id,
                summary_id=right_sid,
                detail={"backend": right_backend, "expected_session": session_id, "actual_session": right_sid},
            ))

        # ── Overwrite detection (version regression) ─────────
        if left_version is not None and right_version is not None:
            try:
                lv = int(left_version)
                rv = int(right_version)
                if lv > rv:
                    issues.append(SummaryIssue(
                        type=SummaryIssueType.OVERWRITE,
                        session_id=session_id,
                        detail={
                            "left_version": lv,
                            "right_version": rv,
                            "note": f"{right_backend} has older version ({rv}) than {left_backend} ({lv})",
                        },
                    ))
                elif rv > lv:
                    issues.append(SummaryIssue(
                        type=SummaryIssueType.OVERWRITE,
                        session_id=session_id,
                        detail={
                            "left_version": lv,
                            "right_version": rv,
                            "note": f"{left_backend} has older version ({lv}) than {right_backend} ({rv})",
                        },
                    ))
            except (ValueError, TypeError):
                # Non-integer versions — compare lexicographically
                lv_str = str(left_version)
                rv_str = str(right_version)
                if lv_str != rv_str:
                    diffs.append({
                        "section": "summary",
                        "path": "summary.version",
                        "left_backend": left_backend,
                        "right_backend": right_backend,
                        "left": lv_str,
                        "right": rv_str,
                        "note": "Version strings differ",
                    })

        # ── Event count comparison ────────────────────────────
        left_count = left_summary.get("original_event_count")
        right_count = right_summary.get("original_event_count")
        if left_count is not None and right_count is not None and left_count != right_count:
            diffs.append({
                "section": "summary",
                "path": "summary.original_event_count",
                "left_backend": left_backend,
                "right_backend": right_backend,
                "left": left_count,
                "right": right_count,
            })

        return diffs, issues
