# Tencent is pleased to support the open source community by making tRPC-Agent-Python available.
#
# Copyright (C) 2026 Tencent. All rights reserved.
#
# tRPC-Agent-Python is licensed under Apache-2.0.
"""Post-processing filters for the code review dry-run example."""

from __future__ import annotations

import hashlib
import re
from typing import Any

from .schemas import ChangedLineKind
from .schemas import Confidence
from .schemas import FilterDecision
from .schemas import ParsedDiff
from .schemas import ReviewFinding

_SECRET_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)(\b(?:api[_-]?key|apikey|token|secret|password)\b\s*[:=]\s*['\"])([^'\"]+)(['\"])", re.DOTALL),
    re.compile(r"(?i)(['\"](?:api[_-]?key|apikey|token|secret|password)['\"]\s*:\s*['\"])([^'\"]+)(['\"])", re.DOTALL),
    re.compile(r"(?i)(\bAuthorization\s*[:=]\s*(?:['\"]?Bearer\s+)?)([A-Za-z0-9._\-]+)(['\"]?)"),
    re.compile(r"(?i)(\bCookie\s*[:=]\s*)([^\s'\"]+)"),
    re.compile(r"(?i)(://[^:/\s]+:)([^@/\s]+)(@)"),
    re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{8,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{8,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{12,}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
)


class PostFilterResult(tuple):
    """Tuple-compatible post-filter result with redaction metadata."""

    redaction_count: int

    def __new__(
        cls,
        findings: list[ReviewFinding],
        warnings: list[ReviewFinding],
        decisions: list[FilterDecision],
        redaction_count: int,
    ) -> "PostFilterResult":
        instance = super().__new__(cls, (findings, warnings, decisions))
        instance.redaction_count = redaction_count
        return instance

    @property
    def findings(self) -> list[ReviewFinding]:
        """Return kept findings."""
        return self[0]

    @property
    def warnings(self) -> list[ReviewFinding]:
        """Return warnings."""
        return self[1]

    @property
    def decisions(self) -> list[FilterDecision]:
        """Return filter decisions."""
        return self[2]


_GLOBAL_REDACTION_COUNTER = 0


def build_added_line_index(parsed_diff: ParsedDiff) -> set[tuple[str, int]]:
    """Build an index of added new-file line anchors."""
    anchors: set[tuple[str, int]] = set()
    for diff_file in parsed_diff.files:
        if not diff_file.new_path:
            continue
        for hunk in diff_file.hunks:
            for line in hunk.changed_lines:
                if line.kind == ChangedLineKind.ADDED and line.new_line_number is not None:
                    anchors.add((diff_file.new_path, line.new_line_number))
    return anchors


def redact_text(text: str) -> str:
    """Redact common secret-like values from text."""
    redacted, _ = redact_text_with_count(text)
    return redacted


def redact_text_with_count(text: str) -> tuple[str, int]:
    """Redact text and return the number of applied replacements."""
    redacted = text
    count = 0
    for pattern in _SECRET_VALUE_PATTERNS:
        if "PRIVATE KEY" in pattern.pattern:
            redacted, applied = pattern.subn("<REDACTED_PRIVATE_KEY>", redacted)
            count += applied
            continue
        if pattern.groups >= 3:
            redacted, applied = pattern.subn(
                lambda match: f"{match.group(1)}<REDACTED_SECRET>{match.group(3) if len(match.groups()) >= 3 else ''}",
                redacted,
            )
            count += applied
            continue
        redacted, applied = pattern.subn("<REDACTED_SECRET>", redacted)
        count += applied
    return redacted, count


def redact_mapping(value: Any) -> Any:
    """Recursively redact string values in JSON-like data."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_mapping(item) for key, item in value.items()}
    return value


def fingerprint_finding(finding: ReviewFinding) -> str:
    """Create a stable fingerprint for a finding."""
    basis = "|".join(
        [
            finding.file.strip().lower(),
            str(finding.line),
            finding.category.strip().lower(),
            finding.title.strip().lower(),
            redact_text(finding.evidence).strip().lower(),
        ]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def apply_post_filters(
    findings: list[ReviewFinding], parsed_diff: ParsedDiff
) -> PostFilterResult:
    """Apply redaction, confidence routing, changed-line anchoring, and dedupe filters."""
    anchors = build_added_line_index(parsed_diff)
    kept: list[ReviewFinding] = []
    warnings: list[ReviewFinding] = []
    decisions: list[FilterDecision] = []
    seen_line_keys: set[tuple[str, int, str]] = set()
    seen_content_keys: set[tuple[str, str, str, str]] = set()
    redaction_count = 0

    for finding in findings:
        redacted, applied = _redact_finding(finding)
        redaction_count += applied
        fingerprint = redacted.fingerprint or fingerprint_finding(redacted)
        redacted = redacted.model_copy(update={"fingerprint": fingerprint})

        if applied:
            decisions.append(
                FilterDecision(
                    filter_name="redaction",
                    decision="redact",
                    reason="Redacted secret-like content from finding fields.",
                    file=redacted.file,
                    line=redacted.line,
                    fingerprint=fingerprint,
                    stage="post",
                )
            )

        line_key = (redacted.file, redacted.line, redacted.category)
        content_key = (redacted.file, redacted.category, redacted.title, redacted.evidence)
        if line_key in seen_line_keys or content_key in seen_content_keys:
            decisions.append(
                FilterDecision(
                    filter_name="dedupe",
                    decision="merge",
                    reason="Duplicate finding for same file/line/category or same normalized evidence already kept.",
                    file=redacted.file,
                    line=redacted.line,
                    fingerprint=fingerprint,
                    stage="post",
                )
            )
            continue

        if redacted.confidence == Confidence.LOW:
            warning = redacted.model_copy(update={"needs_human_review": True})
            warnings.append(warning)
            seen_line_keys.add(line_key)
            seen_content_keys.add(content_key)
            decisions.append(
                FilterDecision(
                    filter_name="confidence",
                    decision="needs_human_review",
                    reason="Low-confidence finding requires human review.",
                    file=redacted.file,
                    line=redacted.line,
                    fingerprint=fingerprint,
                    stage="post",
                )
            )
            continue

        if (redacted.file, redacted.line) not in anchors:
            warning = redacted.model_copy(update={"needs_human_review": True})
            warnings.append(warning)
            seen_line_keys.add(line_key)
            seen_content_keys.add(content_key)
            decisions.append(
                FilterDecision(
                    filter_name="changed_line_anchor",
                    decision="needs_human_review",
                    reason="Finding is not anchored to an added changed line.",
                    file=redacted.file,
                    line=redacted.line,
                    fingerprint=fingerprint,
                    stage="post",
                )
            )
            continue

        seen_line_keys.add(line_key)
        seen_content_keys.add(content_key)
        kept.append(redacted)
        decisions.append(
            FilterDecision(
                filter_name="changed_line_anchor",
                decision="allow",
                reason="Finding is anchored to an added changed line.",
                file=redacted.file,
                line=redacted.line,
                fingerprint=fingerprint,
                stage="post",
            )
        )

    return PostFilterResult(kept, warnings, decisions, redaction_count)


def _redact_finding(finding: ReviewFinding) -> tuple[ReviewFinding, int]:
    count = 0
    updates: dict[str, str | None] = {}
    for field in ("title", "evidence", "recommendation"):
        redacted, applied = redact_text_with_count(getattr(finding, field))
        updates[field] = redacted
        count += applied
    if finding.raw_source:
        redacted, applied = redact_text_with_count(finding.raw_source)
        updates["raw_source"] = redacted
        count += applied
    else:
        updates["raw_source"] = None
    return finding.model_copy(update=updates), count
