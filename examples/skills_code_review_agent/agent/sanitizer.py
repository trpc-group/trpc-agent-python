"""Finding validation, secret redaction, and de-duplication."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from .models import Finding, ReviewInput

_SECRET_PATTERNS = [
    (
        re.compile(r"(?i)\b((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?)://[^:\s/@]+:)([^@\s/]+)(@)"),
        r"\1[REDACTED_PASSWORD]\3",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?key|token|password|passwd|secret)"
            r"\b(\s*[:=]\s*)(?:['\"][^'\"]+['\"]|[^\s,;]+)"
        ),
        r"\1\2[REDACTED_CREDENTIAL]",
    ),
    (
        re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_-]{12,}|"
            r"github_pat_[A-Za-z0-9_-]{12,})\b"
        ),
        "[REDACTED_TOKEN]",
    ),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_JWT]",
    ),
]
_SEVERITIES = {"critical", "high", "medium", "low", "info"}
_CATEGORIES = {"security", "correctness", "reliability", "performance", "maintainability", "testing", "style"}


def redact_sensitive_text(value: Any) -> str:
    if not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            value = repr(value)
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def _dedupe_key(finding: Finding) -> str:
    raw = f"{finding.file}\0{finding.line}\0{finding.category}".encode()
    return hashlib.sha256(raw).hexdigest()


def normalize_findings(
    candidates: Iterable[Finding | dict[str, Any]],
    review_input: ReviewInput,
    *,
    confidence_threshold: float = 0.65,
) -> tuple[list[Finding], list[str], list[Finding]]:
    accepted: dict[tuple[str, int, str], Finding] = {}
    human: dict[tuple[str, int, str], Finding] = {}
    warnings: list[str] = []
    valid_lines = {name: set(lines) for name, lines in review_input.candidate_lines.items()}
    for index, candidate in enumerate(candidates):
        try:
            finding = candidate if isinstance(candidate, Finding) else Finding.model_validate(candidate)
            finding.evidence = redact_sensitive_text(finding.evidence)
            finding.recommendation = redact_sensitive_text(finding.recommendation)
            finding.title = redact_sensitive_text(finding.title)
            invalid = (
                finding.severity not in _SEVERITIES
                or finding.category not in _CATEGORIES
                or finding.file not in valid_lines
                or finding.line not in valid_lines[finding.file]
                or not finding.evidence.strip()
            )
            if invalid or finding.confidence < confidence_threshold:
                finding.needs_human_review = True
            finding.dedupe_key = _dedupe_key(finding)
            key = (finding.file, finding.line, finding.category)
            if finding.needs_human_review:
                previous = human.get(key)
                if previous is None or (finding.confidence, len(finding.evidence)) > (
                    previous.confidence,
                    len(previous.evidence),
                ):
                    human[key] = finding
                continue
            previous = accepted.get(key)
            if previous is None or (finding.confidence, len(finding.evidence)) > (
                previous.confidence,
                len(previous.evidence),
            ):
                accepted[key] = finding
        except Exception as exc:
            warnings.append(f"candidate {index} could not be normalized: {type(exc).__name__}")
    return list(accepted.values()), warnings, list(human.values())
