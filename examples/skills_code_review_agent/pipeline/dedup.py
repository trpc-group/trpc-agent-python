"""Deduplication — remove duplicate findings and reduce noise.

Implements three-tier confidence classification:
  - High-confidence (>= 0.8): automated findings, reliable
  - Warning (>= 0.55): likely issues, needs attention
  - Needs human review (< 0.55): possible false positive
"""

from .types import Finding

# Three-tier confidence thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.8
WARNING_THRESHOLD = 0.55


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
            if f.confidence > seen[fp].confidence:
                seen[fp] = f
        else:
            seen[fp] = f

    severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
    result = sorted(
        seen.values(),
        key=lambda f: (severity_order.get(f.severity.value, 99), -f.confidence),
    )

    return result


def confidence_tier(finding: Finding) -> str:
    """Return the confidence tier for a single finding.

    Returns:
        'high' (>= 0.8), 'warning' (>= 0.55), or 'needs_human_review' (< 0.55).
    """
    if finding.confidence >= HIGH_CONFIDENCE_THRESHOLD:
        return "high"
    elif finding.confidence >= WARNING_THRESHOLD:
        return "warning"
    return "needs_human_review"


def separate_by_tiers(findings: list[Finding]) -> dict[str, list[Finding]]:
    """Split findings into three confidence tiers.

    Args:
        findings: Deduplicated findings.

    Returns:
        Dict with keys 'high', 'warning', 'needs_human_review'.
    """
    tiers: dict[str, list[Finding]] = {
        "high": [],
        "warning": [],
        "needs_human_review": [],
    }
    for f in findings:
        tiers[confidence_tier(f)].append(f)
    return tiers


def separate_low_confidence(
    findings: list[Finding],
    threshold: float = 0.5,
) -> tuple[list[Finding], list[Finding]]:
    """Split findings into high-confidence and low-confidence groups.

    Legacy API — prefer separate_by_tiers() for three-tier classification.

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


def tier_summary(findings: list[Finding]) -> dict:
    """Generate a summary of confidence tiers.

    Args:
        findings: List of findings.

    Returns:
        Dict with counts per tier and total.
    """
    tiers = separate_by_tiers(findings)
    return {
        "total": len(findings),
        "high_confidence": len(tiers["high"]),
        "warning": len(tiers["warning"]),
        "needs_human_review": len(tiers["needs_human_review"]),
    }
