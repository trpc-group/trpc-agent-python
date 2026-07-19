"""Finding dedupe and noise-control helpers."""

from __future__ import annotations

import hashlib
import re

from .review_types import (
    FindingDisposition,
    ReviewFinding,
    ReviewSeverity,
)

_SEVERITY_RANK = {
    ReviewSeverity.CRITICAL: 5,
    ReviewSeverity.HIGH: 4,
    ReviewSeverity.MEDIUM: 3,
    ReviewSeverity.LOW: 2,
    ReviewSeverity.INFO: 1,
}


def dedupe_and_classify_findings(findings: list[ReviewFinding]) -> list[ReviewFinding]:
    """Dedupe findings and assign output buckets by confidence."""

    deduped: dict[tuple[str, str, int | None, str], ReviewFinding] = {}
    for finding in findings:
        key = (
            finding.category.value,
            finding.file,
            finding.line,
            _normalize_evidence(finding.evidence),
        )
        existing = deduped.get(key)
        if existing is None or _should_replace(existing, finding):
            deduped[key] = finding

    processed = list(deduped.values())
    for finding in processed:
        finding.fingerprint = _build_fingerprint(finding)
        if finding.disposition == FindingDisposition.FINDING:
            finding.disposition = _classify_disposition(finding.confidence)

    processed.sort(
        key=lambda item: (
            -_SEVERITY_RANK[item.severity],
            item.file,
            item.line if item.line is not None else -1,
            item.title,
        )
    )
    return processed


def _should_replace(current: ReviewFinding, candidate: ReviewFinding) -> bool:
    """Return whether a candidate finding should replace the current one."""

    if candidate.confidence != current.confidence:
        return candidate.confidence > current.confidence
    if candidate.severity != current.severity:
        return _SEVERITY_RANK[candidate.severity] > _SEVERITY_RANK[current.severity]
    return len(candidate.evidence) > len(current.evidence)


def _classify_disposition(confidence: float) -> FindingDisposition:
    """Map confidence values to output buckets."""

    if confidence >= 0.8:
        return FindingDisposition.FINDING
    if confidence >= 0.4:
        return FindingDisposition.NEEDS_HUMAN_REVIEW
    return FindingDisposition.WARNING


def _normalize_evidence(evidence: str) -> str:
    """Normalize evidence for dedupe keys."""

    compact = re.sub(r"\s+", " ", evidence.strip().lower())
    return compact


def _build_fingerprint(finding: ReviewFinding) -> str:
    """Build a stable fingerprint for downstream storage."""

    payload = "|".join(
        (
            finding.category.value,
            finding.file,
            str(finding.line),
            finding.title,
            _normalize_evidence(finding.evidence),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
