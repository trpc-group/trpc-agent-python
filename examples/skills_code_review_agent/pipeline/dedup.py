"""Deduplication — remove duplicate findings and reduce noise."""

from .types import Finding


def deduplicate(findings: list[Finding]) -> list[Finding]:
    """Remove duplicate findings based on fingerprint.

    Two findings are duplicates if they share the same
    (file, line, category, title) tuple. The one with higher
    confidence is kept.

    Args:
        findings: Raw findings list.

    Returns:
        Deduplicated findings sorted by severity then confidence.
    """
    seen: dict[str, Finding] = {}

    for f in findings:
        fp = f.fingerprint()
        if fp in seen:
            # Keep the finding with higher confidence
            if f.confidence > seen[fp].confidence:
                seen[fp] = f
        else:
            seen[fp] = f

    # Sort: critical > high > medium > low > info
    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    result = sorted(
        seen.values(),
        key=lambda f: (severity_order.get(f.severity.value, 99), -f.confidence),
    )

    return result


def separate_low_confidence(
    findings: list[Finding],
    threshold: float = 0.5,
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into high-confidence and low-confidence groups.

    Low-confidence findings go to warnings/needs_human_review.

    Args:
        findings: Deduplicated findings.
        threshold: Confidence threshold.

    Returns:
        (high_confidence_findings, low_confidence_findings)
    """
    high: list[Finding] = []
    low: list[Finding] = []

    for f in findings:
        if f.confidence >= threshold:
            high.append(f)
        else:
            low.append(f)

    return high, low
