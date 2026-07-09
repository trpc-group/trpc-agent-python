"""SARIF (Static Analysis Results Interchange Format) output.

Produces SARIF v2.1.0 compatible output for integration with GitHub
Code Scanning, Azure DevOps, and other SARIF-compatible tools.
"""

import json
from datetime import datetime, timezone

from .types import Finding, ReviewReport
from .dedup import separate_by_tiers


# SARIF severity mapping
_SEVERITY_TO_SARIF = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}

# SARIF rule index base URL
_RULE_HELP_URI_BASE = "https://github.com/trpc-group/trpc-agent-python/blob/main/skills/code-review/docs/rules.md"


def generate_sarif(report: ReviewReport, source_root: str = "") -> str:
    """Generate a SARIF v2.1.0 report from review findings.

    Args:
        report: Complete review report.
        source_root: Optional root URI for source files.

    Returns:
        SARIF JSON string compliant with SARIF v2.1.0.
    """
    tiers = separate_by_tiers(report.findings)
    rules = _build_rules(report.findings)
    results = _build_results(report.findings, source_root)

    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Code Review Agent",
                        "version": "1.0.0",
                        "informationUri": "https://github.com/trpc-group/trpc-agent-python",
                        "rules": rules,
                        "organization": "tRPC Group",
                    },
                },
                "invocations": [
                    {
                        "executionSuccessful": True,
                        "startTimeUtc": datetime.now(timezone.utc).isoformat(),
                        "endTimeUtc": datetime.now(timezone.utc).isoformat(),
                    },
                ],
                "results": results,
                "properties": {
                    "task_id": report.task_id,
                    "high_confidence_count": len(tiers["high"]),
                    "warning_count": len(tiers["warning"]),
                    "needs_human_review_count": len(tiers["needs_human_review"]),
                },
            },
        ],
    }
    return json.dumps(sarif, indent=2, ensure_ascii=False)


def _build_rules(findings: list[Finding]) -> list[dict]:
    """Build SARIF rules from findings, deduplicating by category+title."""
    seen: dict[str, dict] = {}
    for f in findings:
        rule_id = _rule_id(f)
        if rule_id not in seen:
            seen[rule_id] = {
                "id": rule_id,
                "name": f.title,
                "shortDescription": {"text": f.title},
                "fullDescription": {"text": f.recommendation},
                "helpUri": f"{_RULE_HELP_URI_BASE}#{rule_id.lower()}",
                "properties": {
                    "category": f.category.value,
                    "confidence": f.confidence,
                },
            }
    return list(seen.values())


def _build_results(findings: list[Finding], source_root: str = "") -> list[dict]:
    """Build SARIF results from findings."""
    results: list[dict] = []
    for f in findings:
        result = {
            "ruleId": _rule_id(f),
            "ruleIndex": 0,
            "level": _SEVERITY_TO_SARIF.get(f.severity.value, "warning"),
            "message": {
                "text": f"{f.title}\n\nRecommendation: {f.recommendation}",
            },
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": f.file,
                            "uriBaseId": "%SRCROOT%" if source_root else "",
                        },
                        "region": {
                            "startLine": f.line,
                            "startColumn": 1,
                        },
                    },
                },
            ],
            "properties": {
                "confidence": f.confidence,
                "source": f.source,
                "category": f.category.value,
            },
        }
        if f.evidence and f.evidence != "[REDACTED]":
            result["locations"][0]["physicalLocation"]["region"]["snippet"] = {
                "text": f.evidence[:200],
            }
        results.append(result)
    return results


def _rule_id(finding: Finding) -> str:
    """Generate a stable rule ID from a finding."""
    import re
    safe_cat = re.sub(r"[^A-Za-z0-9]", "_", finding.category.value).upper()
    safe_title = re.sub(r"[^A-Za-z0-9]", "_", finding.title)[:40]
    return f"{safe_cat}_{safe_title}"
